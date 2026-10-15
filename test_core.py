import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from calculator import calculate
from mfds_client import MFDSClient, ServiceError, decode_response
from config import ROOT


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.products = json.loads((ROOT / 'static/sample.json').read_text(encoding='utf-8'))
        self.products[0]['quantity'] = '2'
        self.products[1]['quantity'] = '1'

    def test_sum_and_g_conversion(self):
        result = calculate(self.products)
        self.assertEqual(result['ingredients'][0]['total_mg'], '250')
        self.assertEqual(result['ingredients'][1]['total_mg'], '25.000')
        self.assertTrue(result['ingredients'][0]['duplicate'])
        self.assertEqual(result['ingredients'][0]['criterion_status'], 'unknown')

    def test_two_tablet_basis(self):
        self.products[0]['ingredients'][0]['basis_quantity'] = '2'
        self.assertEqual(calculate(self.products)['ingredients'][0]['total_mg'], '150')

    def test_microgram_conversion(self):
        self.products[0]['ingredients'][0]['unit'] = 'μg'
        self.assertEqual(calculate(self.products)['ingredients'][0]['total_mg'], '50.200')

    def test_liquid(self):
        p = self.products[0]
        p['quantity_unit'] = p['ingredients'][0]['basis_unit'] = 'mL'
        p['quantity'] = '5'
        p['ingredients'][0]['basis_quantity'] = '100'
        self.assertEqual(calculate([p])['ingredients'][0]['total_mg'], '5')

    def test_invalid_inputs_blocked(self):
        for value in ('NaN', 'Infinity', '-1', '', None):
            with self.subTest(value=value):
                p = copy.deepcopy(self.products)
                p[0]['ingredients'][0]['amount'] = value
                with self.assertRaises(ServiceError):
                    calculate(p)

    def test_missing_basis(self):
        self.products[0]['ingredients'][0]['basis_quantity'] = ''
        with self.assertRaises(ServiceError):
            calculate(self.products)

    def test_mismatched_dimension(self):
        self.products[0]['ingredients'][0]['basis_unit'] = 'mL'
        with self.assertRaises(ServiceError):
            calculate(self.products)

    def test_duplicate_product(self):
        with self.assertRaises(ServiceError):
            calculate([self.products[0], self.products[0]])

    def test_duplicate_component_row(self):
        self.products[0]['ingredients'] *= 2
        with self.assertRaises(ServiceError):
            calculate(self.products)

    def test_unconfirmed_blocked(self):
        self.products[0]['confirmed'] = False
        with self.assertRaises(ServiceError):
            calculate(self.products)

    def test_zero_excluded(self):
        self.products[0]['quantity'] = 0
        result = calculate(self.products)
        self.assertEqual(result['ingredients'][0]['total_mg'], '50')
        self.assertFalse(result['ingredients'][0]['duplicate'])

    def test_synonyms_not_guessed(self):
        # 새 요구사항에서는 공백 차이를 합칩니다. 다른 염형은 여전히 합치지 않습니다.
        self.products[1]['ingredients'][0]['name'] = '가상성분 X 염산염'
        self.assertEqual(len(calculate(self.products)['ingredients']), 3)

    def test_only_reviewed_scoped_rule(self):
        for p in self.products:
            p['ingredients'][0]['ingredient_code'] = 'fictional-x'
        rule = {'id': 'TEST-ONLY', 'ingredient_code': 'fictional-x', 'mode': 'single',
                'population': 'adult', 'reviewer': 'TEST', 'reviewed_at': '2026-10-15',
                'source_url': 'https://example.test', 'product_codes': ['demo-a', 'demo-b'],
                'limit_mg': '200', 'reviewed': False}
        self.assertEqual(calculate(self.products, rules=[rule])['ingredients'][0]['criterion_status'], 'unknown')
        rule['reviewed'] = True
        self.assertEqual(calculate(self.products, rules=[rule])['ingredients'][0]['criterion_status'], 'exceeded')
        self.assertEqual(calculate(self.products, mode='day', rules=[rule])['ingredients'][0]['criterion_status'], 'unknown')
        rule['product_codes'] = ['demo-a']
        self.assertEqual(calculate(self.products, rules=[rule])['ingredients'][0]['criterion_status'], 'unknown')


class APITests(unittest.TestCase):
    def test_json_envelopes(self):
        for items in ([{'ITEM_SEQ': '123'}], {'item': [{'ITEM_SEQ': '123'}]}, {'item': {'ITEM_SEQ': '123'}}):
            raw = json.dumps({'header': {'resultCode': '00'}, 'body': {'items': items, 'totalCount': 1}}).encode()
            self.assertEqual(decode_response(raw)['items'][0]['ITEM_SEQ'], '123')

    def test_empty_not_auth_error(self):
        raw = b'{"header":{"resultCode":"00"},"body":{"items":[],"totalCount":0}}'
        self.assertEqual(decode_response(raw)['items'], [])

    def test_xml(self):
        raw = b'<response><header><resultCode>00</resultCode></header><body><items><item><ITEM_SEQ>0123</ITEM_SEQ></item></items><totalCount>1</totalCount></body></response>'
        self.assertEqual(decode_response(raw)['items'][0]['ITEM_SEQ'], '0123')

    def test_xml_auth_error(self):
        with self.assertRaises(ServiceError):
            decode_response(b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>')

    def test_missing_key(self):
        with patch('mfds_client.settings', return_value={}):
            with self.assertRaises(ServiceError):
                MFDSClient().search('test')

    def test_encoding_and_parameters(self):
        requests = []
        def transport(req, timeout):
            requests.append(req.full_url)
            return io.BytesIO(b'{"header":{"resultCode":"00"},"body":{"items":[],"totalCount":0}}')
        with patch('mfds_client.settings', return_value={'MFDS_SERVICE_KEY': 'test%2Bkey%3D'}):
            MFDSClient(transport).search('가상 약')
        parsed = urlsplit(requests[0])
        self.assertTrue(parsed.path.endswith('/getDrugPrdtPrmsnInq08'))
        self.assertEqual(parse_qs(parsed.query)['serviceKey'], ['test+key='])
        self.assertEqual(parse_qs(parsed.query)['item_name'], ['가상 약'])

    def test_detail_filters_wrong_product(self):
        client = MFDSClient()
        with patch.object(client, 'query', return_value={'items': [{'ITEM_SEQ': '999'}]}):
            with self.assertRaises(ServiceError):
                client.detail('123')

    def test_detail_partial_failure_preserves_product(self):
        client = MFDSClient()
        with patch.object(client, 'query', side_effect=[{'items': [{'ITEM_SEQ': '123'}]}, ServiceError('연결 오류')]):
            result = client.detail('123')
            self.assertEqual(result['product']['ITEM_SEQ'], '123')
            self.assertTrue(result['warnings'])


if __name__ == '__main__':
    unittest.main()
