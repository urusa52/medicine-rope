import unittest
from matcher import normalize_name, parse_strength, parse_basis, compare_ingredients, official_calculation_product
from calculator import calculate
from mfds_client import ServiceError


def photo(amount='500mg', name='아세트아미노펜'):
    return {'product_name_raw': '가상 제품', 'manufacturer': '가상제약',
            'effective_ingredients': [{'ingredient_name': name, 'base_ingredient_name': name,
                                       'strength_per_unit': amount, 'ingredient_standard': 'KP'}]}


def detail(qnt='500', unit='mg', name='아세트아미노펜', code='123', basis='1정 중'):
    return {'product': {'ITEM_SEQ': code, 'ITEM_NAME': '가상 제품', 'ENTP_NAME': '가상제약',
                         'TOTAL_CONTENT': basis},
            'ingredients_raw': [{'MTRAL_NM': name, 'QNT': qnt, 'INGD_UNIT_CD': unit,
                                 'MTRAL_CODE': 'ingredient-test', 'CPNT_CTNT_CONT': basis}],
            'warnings': [], 'source': 'https://example.test'}


class MatcherTests(unittest.TestCase):
    def test_spaces_standard_case(self):
        self.assertEqual(normalize_name(' Acetaminophen ( USP ) '), 'acetaminophen')
        self.assertEqual(normalize_name('아세트 아미노펜(KP)'), '아세트아미노펜')

    def test_meaningful_parentheses_preserved(self):
        self.assertNotEqual(normalize_name('성분(염산염)'), normalize_name('성분'))

    def test_supported_strength_formats(self):
        for value in ['500mg','500 밀리그램','0.5g','1정 중 500mg']:
            with self.subTest(value=value):
                self.assertEqual(parse_strength(value)['mg'], '500' if '0.5' not in value else '500.0')

    def test_micrograms(self):
        self.assertEqual(parse_strength('500마이크로그램')['mg'], '0.500')

    def test_unknown_and_ambiguous_strength(self):
        for value in [None, {}, '', '500', '500 IU', 'NaNmg', '-1mg', '500mg + 50mg', '500mg/100mL', '1일 500mg']:
            with self.subTest(value=value):
                self.assertIsNone(parse_strength(value)['mg'])

    def test_basis(self):
        self.assertEqual(parse_basis('1정(총량 700mg) 중'), ('1','정'))
        self.assertEqual(parse_basis('100mL 중'), ('100','mL'))
        self.assertEqual(parse_basis('용량 불명'), (None,None))

    def test_basis_conflict(self):
        d=detail();d['product']['TOTAL_CONTENT']='2정 중'
        result=compare_ingredients(photo(),d)
        self.assertFalse(result['calculation_ready'])

    def test_exact_decimal_match(self):
        result=compare_ingredients(photo('0.5g'),detail())
        self.assertEqual(result['status'],'일치')
        self.assertTrue(result['calculation_ready'])

    def test_no_tolerance(self):
        result=compare_ingredients(photo('499.999mg'),detail())
        self.assertEqual(result['rows'][0]['status'],'함량 불일치')

    def test_per_unit_basis_conversion(self):
        result=compare_ingredients(photo('2정 중 1000mg'),detail())
        self.assertEqual(result['rows'][0]['status'],'일치')

    def test_different_basis_dimension_unknown(self):
        self.assertEqual(compare_ingredients(photo('100mL 중 500mg'),detail())['rows'][0]['status'],'함량 미확인')

    def test_photo_only_official_only(self):
        result=compare_ingredients(photo(name='다른 성분'),detail())
        self.assertEqual([r['status'] for r in result['rows']],['사진에만 있음','공식에만 있음'])

    def test_unknown_amount(self):
        self.assertEqual(compare_ingredients(photo('500'),detail())['rows'][0]['status'],'함량 미확인')

    def test_missing_photo_fields(self):
        for data in [None,{}, {'effective_ingredients':None},{'effective_ingredients':[None,{}]}]:
            with self.subTest(data=data):
                self.assertEqual(compare_ingredients(data,detail())['status'],'확인 필요')

    def test_no_data_not_match(self):
        self.assertEqual(compare_ingredients({}, {})['status'],'확인 필요')

    def test_unknown_official_unit(self):
        result=compare_ingredients(photo(),detail(unit='001'))
        self.assertFalse(result['calculation_ready'])
        self.assertEqual(result['rows'][0]['status'],'함량 미확인')

    def test_calculation_uses_official_amount(self):
        p=photo('650mg');d=detail('500');r=compare_ingredients(p,d)
        product=official_calculation_product(d,r,p)
        self.assertEqual(product['ingredients'][0]['amount'],'500')
        self.assertEqual(product['ingredients'][0]['ingredient_code'],'ingredient-test')
        self.assertIn('650mg',product['ingredients'][0]['evidence'])

    def test_unclear_official_basis_blocks(self):
        d=detail(basis='불명');r=compare_ingredients(photo(),d)
        with self.assertRaises(ServiceError):
            official_calculation_product(d,r,photo())

    def test_official_duplicate_blocks(self):
        d=detail();d['ingredients_raw']*=2
        self.assertFalse(compare_ingredients(photo(),d)['calculation_ready'])

    def test_name_grouping(self):
        d=detail(name=' Acetaminophen (USP) ');r=compare_ingredients(photo(name='acetaminophen'),d)
        p=official_calculation_product(d,r,photo());p['ingredients'][0]['ingredient_code']='';p['quantity']='1'
        import copy
        q=copy.deepcopy(p);q['code']='456';q['ingredients'][0]['name']='acetaminophen'
        result=calculate([p,q])
        self.assertEqual(len(result['ingredients']),1)
        self.assertEqual(result['ingredients'][0]['matching'],'이름 기준으로 묶음')

    def test_optional_product_codes(self):
        p=official_calculation_product(detail(),compare_ingredients(photo(),detail()),photo());p['quantity']='1'
        rule={'reviewed':True,'ingredient_code':'ingredient-test','mode':'single','population':'adult',
              'reviewer':'test','reviewed_at':'2026-10-15','source_url':'https://example.test','limit_mg':'400'}
        self.assertEqual(calculate([p],rules=[rule])['ingredients'][0]['criterion_status'],'exceeded')
        rule['product_codes']=[]
        self.assertEqual(calculate([p],rules=[rule])['ingredients'][0]['criterion_status'],'exceeded')
