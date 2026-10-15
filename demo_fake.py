"""키 없이 화면 흐름을 시연하는 가상 데모: python demo_fake.py

모든 제품·성분·기준값은 가상입니다(실제 약 아님). 식약처·Upstage에 요청하지 않습니다.
사진을 찍으면 순서대로 '가상해열정' → '가상콜드정'을 인식한 것처럼 동작합니다.
"""
import itertools
import time

import main
from daymax_client import DayMaxClient
from workflow import AnalysisWorkflow

PRODUCTS = [
    ('가상해열정500밀리그램', '(주)가상제약', [('아세트아미노펜', 'M000001', '500')]),
    ('가상해열정500밀리그램', '예시약품(주)', [('아세트아미노펜', 'M000001', '500')]),
    ('가상해열이알서방정', '(주)가상제약', [('아세트아미노펜', 'M000001', '650')]),
    ('가상콜드정', '샘플제약(주)', [('아세트아미노펜', 'M000001', '300'), ('슈도에페드린염산염', 'M000002', '30')]),
    ('가상해열산', '테스트팜(주)', [('아세트아미노펜', 'M000001', '500')]),
    ('가상종합감기정', '테스트팜(주)', [('아세트아미노펜', 'M000001', '250'), ('카페인무수물', 'M000003', '25')]),
]
PHOTOS = itertools.cycle([
    {'product_name_raw': '가상해열정500밀리그램', 'manufacturer': '가상제약',
     'effective_ingredients': [{'ingredient_name': '아세트아미노펜', 'strength_per_unit': '500㎎'}]},
    {'product_name_raw': '가상콜드정', 'manufacturer': '샘플제약',
     'effective_ingredients': [{'ingredient_name': '아세트아미노펜', 'strength_per_unit': '300mg'},
                               {'ingredient_name': '슈도에페드린염산염', 'strength_per_unit': '30mg'}]},
])
DAYMAX = [  # 가상 기준값
    {'CPNT_CD': 'M000001', 'DRUG_CPNT_KOR_NM': '아세트아미노펜', 'DOSAGE_ROUTE_CODE': '경구', 'FOML_NM': '정제',
     'DAY_MAX_DOSG_QY': '4000', 'DAY_MAX_DOSG_QY_UNIT': 'mg'},
    {'CPNT_CD': 'M000002', 'DRUG_CPNT_KOR_NM': '슈도에페드린염산염', 'DOSAGE_ROUTE_CODE': '경구', 'FOML_NM': '정제',
     'DAY_MAX_DOSG_QY': '240', 'DAY_MAX_DOSG_QY_UNIT': 'mg'},
]


class FakeMFDS:
    def search(self, name, page=1, manufacturer=None, page_size=50):
        hits = [p for p in PRODUCTS if name.replace(' ', '') in p[0]] or PRODUCTS
        items = [{'ITEM_SEQ': str(201900001 + PRODUCTS.index(p)), 'ITEM_NAME': p[0], 'ENTP_NAME': p[1]} for p in hits]
        return {'items': items, 'total': len(items), 'source': '가상 데모'}

    def detail(self, code):
        name, maker, ingredients = PRODUCTS[int(code) - 201900001]
        product = {'ITEM_SEQ': code, 'ITEM_NAME': name, 'ENTP_NAME': maker, 'CHART': '흰색의 원형 정제',
                   'TOTAL_CONTENT': '1정 중', 'UD_DOC_DATA': '가상 용법', 'NB_DOC_DATA': '가상 주의사항'}
        rows = [{'ITEM_SEQ': code, 'MTRAL_NM': n, 'MTRAL_CODE': c, 'QNT': q, 'INGD_UNIT_CD': '밀리그램',
                 'CPNT_CTNT_CONT': '1정 중'} for n, c, q in ingredients]
        return {'product': product, 'ingredients_raw': rows, 'warnings': [], 'source': '가상 데모'}

    def query(self, operation, base=None, **params):
        if operation == 'getDayMaxDosgQyByIngdInq':
            rows = [r for r in DAYMAX if params.get('CPNT_CD') in (None, r['CPNT_CD'])]
            return {'items': rows, 'total': len(rows), 'source': '가상 데모'}
        return {'items': [], 'total': 0, 'source': '가상 데모'}   # DUR: 가상 데이터 없음


def fake_analyze(content, mime):
    time.sleep(1.5)
    return {'extracted': next(PHOTOS), 'warnings': [], 'selected_model': 'demo'}


def setup():
    fake = FakeMFDS()
    from dur_client import DURClient
    main.WORKFLOW = AnalysisWorkflow(client=fake, analyzer=fake_analyze, daymax=DayMaxClient(fake), dur=DURClient(fake))
    main.DEMO = True


if __name__ == '__main__':
    setup()
    import dur_client, daymax_client
    dur_client.settings = daymax_client.settings = lambda: {}
    main.run()
