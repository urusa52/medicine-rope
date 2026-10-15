"""자동 선택, 버그 수정, 1일 최대투여량·DUR 판정 테스트. 외부 API는 가짜 응답."""
import unittest
from unittest.mock import patch

from matcher import parse_strength, parse_basis, manufacturer_matches
from normalization import normalize_company
from mfds_client import ServiceError
from workflow import AnalysisWorkflow, pick_auto
from daymax_client import DayMaxClient, pick_limit
from dur_client import DURClient
from test_matcher import photo, detail
from test_workflow import FakeClient


class FakeDayMax:
    def __init__(self, table=None, fail=False):
        self.table, self.fail = table or {}, fail
    def lookup(self, code='', name=''):
        if self.fail:
            raise ServiceError('연결 실패')
        return self.table.get(name)


class FakeDUR:
    def __init__(self, findings=None, errors=None):
        self.result = {'findings': findings or [], 'errors': errors or []}
        self.seen = None
    def check(self, products):
        self.seen = products
        return self.result


class FakeQuery:
    def __init__(self, answers):
        self.answers, self.calls = answers, []
    def query(self, operation, base=None, **params):
        self.calls.append((operation, base, params))
        answer = self.answers(operation, params)
        if isinstance(answer, Exception):
            raise answer
        return {'items': answer, 'total': len(answer), 'source': 'fake'}


def acetaminophen_limit(mg='4000'):
    from decimal import Decimal
    return {'acetaminophen': None, '아세트아미노펜': {'limit_mg': Decimal(mg), 'note': '1일 최대투여량(경구)',
                                                  'form': '정제', 'source': 'https://example.test'}}


class FixTests(unittest.TestCase):
    def test_company_forms(self):
        for a in ['종근당', '(주)종근당', '㈜종근당', '주식회사 종근당', '종근당(주)', ' 종근당 ']:
            with self.subTest(a=a):
                self.assertTrue(manufacturer_matches(a, '(주)종근당'))
        self.assertFalse(manufacturer_matches('종근당바이오', '(주)종근당'))
        self.assertEqual(normalize_company(None), '')

    def test_strength_comma_and_compat_units(self):
        self.assertEqual(parse_strength('1,000mg')['mg'], '1000')
        self.assertEqual(parse_strength('500㎎')['mg'], '500')
        self.assertEqual(parse_strength('500㎍')['mg'], '0.500')
        self.assertEqual(parse_strength('500mg')['raw'], '500mg')
        self.assertIsNone(parse_strength('1,00mg')['mg'])

    def test_korean_ml_basis(self):
        self.assertEqual(parse_basis('100밀리리터 중'), ('100', 'mL'))
        self.assertEqual(parse_basis('100㎖ 중'), ('100', 'mL'))

    def test_approved_survives_many_searches(self):
        flow = AnalysisWorkflow(FakeClient(), None, FakeDayMax(), FakeDUR())
        approval = flow.confirm_candidate(flow.match_extracted(photo())['candidates'][0]['candidate_id'], True)
        for _ in range(400):
            flow._put('candidate', {})
        self.assertTrue(flow._get(approval['approval_id'], 'approved'))

    def test_page_warning_only_when_more(self):
        flow = AnalysisWorkflow(FakeClient(), None, FakeDayMax(), FakeDUR())
        self.assertEqual(flow.match_extracted(photo())['warnings'], [])


class AutoSelectTests(unittest.TestCase):
    def flow(self, details):
        products = [d['product'] for d in details.values()]
        return AnalysisWorkflow(FakeClient(products, details), None, FakeDayMax(), FakeDUR())

    def test_single_full_match_is_auto(self):
        result = self.flow({'123': detail()}).match_extracted(photo())
        self.assertEqual(result['auto_candidate_id'], result['candidates'][0]['candidate_id'])

    def test_mismatch_not_auto(self):
        result = self.flow({'123': detail()}).match_extracted(photo('650mg'))
        self.assertIsNone(result['auto_candidate_id'])

    def test_manufacturer_breaks_tie(self):
        a, b = detail(code='101'), detail(code='102')
        a['product']['ENTP_NAME'] = '다른제약'
        b['product']['ENTP_NAME'] = '(주)가상제약'
        result = self.flow({'101': a, '102': b}).match_extracted(photo())
        chosen = [c for c in result['candidates'] if c['candidate_id'] == result['auto_candidate_id']]
        self.assertEqual(chosen[0]['product']['ITEM_SEQ'], '102')

    def test_ambiguous_not_auto(self):
        a, b = detail(code='101'), detail(code='102')
        for d in (a, b):
            d['product']['ENTP_NAME'] = '다른제약'
        result = self.flow({'101': a, '102': b}).match_extracted(photo())
        self.assertIsNone(result['auto_candidate_id'])
        self.assertIn('여러 개', result['auto_reason'])

    def test_not_ready_not_auto(self):
        self.assertEqual(pick_auto([{'candidate_id': 'x', 'manufacturer_match': True,
                                     'comparison': {'all_match': True, 'calculation_ready': False}}])[0], None)


class DayMaxTests(unittest.TestCase):
    rows = [{'CPNT_CD': 'M1', 'DRUG_CPNT_KOR_NM': '아세트아미노펜', 'DOSAGE_ROUTE_CODE': '경구', 'FOML_NM': '정제',
             'DAY_MAX_DOSG_QY': '4', 'DAY_MAX_DOSG_QY_UNIT': 'g'},
            {'CPNT_CD': 'M1', 'DRUG_CPNT_KOR_NM': '아세트아미노펜', 'DOSAGE_ROUTE_CODE': '경구', 'FOML_NM': '서방정',
             'DAY_MAX_DOSG_QY': '3900', 'DAY_MAX_DOSG_QY_UNIT': 'mg'},
            {'CPNT_CD': 'M1', 'DRUG_CPNT_KOR_NM': '아세트아미노펜', 'DOSAGE_ROUTE_CODE': '주사', 'FOML_NM': '주사제',
             'DAY_MAX_DOSG_QY': '100', 'DAY_MAX_DOSG_QY_UNIT': 'mg'},
            {'CPNT_CD': 'M9', 'DRUG_CPNT_KOR_NM': '다른성분', 'DOSAGE_ROUTE_CODE': '경구',
             'DAY_MAX_DOSG_QY': '1', 'DAY_MAX_DOSG_QY_UNIT': 'mg'}]

    def test_oral_minimum(self):
        found = pick_limit(self.rows, 'M1', '')
        self.assertEqual(str(found['limit_mg']), '3900')
        self.assertIn('가장 낮은 값', found['note'])

    def test_name_match_and_unknown_unit(self):
        self.assertEqual(str(pick_limit(self.rows[:1], '', '아세트 아미노펜')['limit_mg']), '4000')
        self.assertIsNone(pick_limit([{**self.rows[0], 'DAY_MAX_DOSG_QY_UNIT': 'IU'}], 'M1'))
        self.assertIsNone(pick_limit([{**self.rows[0], 'DAY_MAX_DOSG_QY': '0.00000'}], 'M1'))

    def test_client_code_then_name_and_cache(self):
        fake = FakeQuery(lambda op, p: [] if 'CPNT_CD' in p else self.rows[:1])
        client = DayMaxClient(fake)
        with patch('daymax_client.settings', return_value={}):
            self.assertEqual(str(client.lookup('M000001', '아세트아미노펜')['limit_mg']), '4000')
            client.lookup('M000001', '아세트아미노펜')
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[0][0], 'getDayMaxDosgQyByIngdInq')
        self.assertTrue(fake.calls[0][1].endswith('/DayMaxDosgQyByIngdService'))


class DURTests(unittest.TestCase):
    products = [{'name': '새약', 'code': '1', 'role': 'new'}, {'name': '먹은약', 'code': '2', 'role': 'taken'}]

    def test_taboo_found_and_other_product_filtered(self):
        def answers(op, p):
            if op == 'getUsjntTabooInfoList03':
                return [{'ITEM_SEQ': '1', 'MIXTURE_ITEM_SEQ': '2', 'TYPE_NAME': '병용금기', 'PROHBT_CONTENT': '함께 금지'},
                        {'ITEM_SEQ': '99', 'TYPE_NAME': '병용금기', 'PROHBT_CONTENT': '다른 제품'}]
            return []
        fake = FakeQuery(answers)
        with patch('dur_client.settings', return_value={}):
            result = DURClient(fake).check(self.products)
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['level'], 'danger')
        taboo = [c for c in fake.calls if c[0] == 'getUsjntTabooInfoList03'][0][2]
        self.assertEqual((taboo['itemName'], taboo['mixtureItemName']), ('새약', '먹은약'))

    def test_failure_is_reported_not_ok(self):
        with patch('dur_client.settings', return_value={}):
            result = DURClient(FakeQuery(lambda op, p: ServiceError('권한 없음'))).check(self.products)
        self.assertEqual(result['findings'], [])
        self.assertTrue(result['errors'])

    def test_efficacy_overlap(self):
        def answers(op, p):
            if op == 'getEfcyDplctInfoList03':
                return [{'EFFECT_NAME': '해열진통제'}]
            return []
        with patch('dur_client.settings', return_value={}):
            result = DURClient(FakeQuery(answers)).check(self.products)
        self.assertEqual(result['findings'][0]['type'], '효능군 중복')


class JudgementTests(unittest.TestCase):
    def setUp(self):
        a, b = detail(code='101'), detail(code='102')
        self.flow = AnalysisWorkflow(FakeClient([a['product'], b['product']], {'101': a, '102': b}), None,
                                     FakeDayMax(acetaminophen_limit()), FakeDUR())
        found = self.flow.match_extracted(photo())
        self.ids = [self.flow.confirm_candidate(c['candidate_id'], True)['approval_id'] for c in found['candidates']]

    def run_case(self, taken_qty, new_qty):
        return self.flow.evaluate([{'approval_id': self.ids[0], 'quantity': taken_qty, 'role': 'taken'},
                                   {'approval_id': self.ids[1], 'quantity': new_qty, 'role': 'new'}],
                                  'day', [], check_safety=True)

    def test_exceeded_says_stop(self):
        result = self.run_case('6', '2')   # 3000 + 1000 = 4000 → 초과 아님(같음)
        self.assertEqual(result['ingredients'][0]['criterion_status'], 'not_exceeded')
        result = self.run_case('6', '3')   # 4500 > 4000
        self.assertEqual(result['judgement']['level'], 'stop')
        row = result['ingredients'][0]
        self.assertEqual((row['taken_mg'], row['new_mg'], row['total_mg']), ('3000', '1500', '4500'))

    def test_duplicate_under_limit_is_check(self):
        self.assertEqual(self.run_case('1', '1')['judgement']['level'], 'check')

    def test_ok_only_without_duplicates_and_known_limits(self):
        result = self.flow.evaluate([{'approval_id': self.ids[1], 'quantity': '2', 'role': 'new'}], 'day', [], True)
        self.assertEqual(result['judgement']['level'], 'ok')
        self.assertEqual(result['ingredients'][0]['percent'], 25)

    def test_unknown_limit_or_lookup_failure_is_check(self):
        self.flow.daymax = FakeDayMax(fail=True)
        result = self.flow.evaluate([{'approval_id': self.ids[1], 'quantity': '1', 'role': 'new'}], 'day', [], True)
        self.assertEqual(result['judgement']['level'], 'check')
        self.assertTrue(result['judgement']['errors'])

    def test_dur_danger_is_stop(self):
        self.flow.dur = FakeDUR([{'type': '병용금기', 'level': 'danger', 'products': ['a', 'b'], 'text': 'x'}])
        result = self.flow.evaluate([{'approval_id': self.ids[1], 'quantity': '1', 'role': 'new'}], 'day', [], True)
        self.assertEqual(result['judgement']['level'], 'stop')

    def test_needs_new_product(self):
        with self.assertRaises(ServiceError):
            self.flow.evaluate([{'approval_id': self.ids[0], 'quantity': '1', 'role': 'taken'}], 'day', [], True)

    def test_local_rule_wins_over_api(self):
        rule = {'reviewed': True, 'ingredient_name': '아세트아미노펜', 'mode': 'day', 'population': 'adult',
                'reviewer': '약사 A', 'reviewed_at': '2026-10-15', 'source_url': 'https://example.test', 'limit_mg': '3000'}
        result = self.flow.evaluate([{'approval_id': self.ids[1], 'quantity': '7', 'role': 'new'}], 'day', [rule], True)
        self.assertEqual(result['ingredients'][0]['limit_mg'], '3000')
        self.assertEqual(result['judgement']['level'], 'stop')


if __name__ == '__main__':
    unittest.main()
