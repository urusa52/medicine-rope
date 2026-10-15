"""식약처 '의약품 성분별 1일 최대투여량 정보' API (data.go.kr 15098095).

요청: https://apis.data.go.kr/1471000/DayMaxDosgQyByIngdService/getDayMaxDosgQyByIngdInq
요청변수: CPNT_CD(성분코드), DRUG_CPNT_KOR_NM(성분명 한글), DRUG_CPNT_ENG_NM
응답: CPNT_CD, DRUG_CPNT_KOR_NM, FOML_NM(제형), DOSAGE_ROUTE_CODE(투여경로), DAY_MAX_DOSG_QY_UNIT, DAY_MAX_DOSG_QY
(공공데이터포털 명세 기준. 같은 성분이라도 제형·투여경로별로 여러 행이 올 수 있습니다.)
"""
import re
import threading
from decimal import Decimal, InvalidOperation

from config import settings
from mfds_client import MFDSClient, ServiceError
from normalization import normalize_name

DEFAULT_BASE = 'https://apis.data.go.kr/1471000/DayMaxDosgQyByIngdService'
OPERATION = 'getDayMaxDosgQyByIngdInq'
UNIT_MG = {'mg': Decimal('1'), '밀리그램': Decimal('1'), 'g': Decimal('1000'), '그램': Decimal('1000'),
           'μg': Decimal('0.001'), 'µg': Decimal('0.001'), 'ug': Decimal('0.001'), 'mcg': Decimal('0.001'),
           '마이크로그램': Decimal('0.001')}


def _limit_mg(row):
    try:
        value = Decimal(str(row.get('DAY_MAX_DOSG_QY', '')).strip())
    except (InvalidOperation, ValueError):
        return None
    factor = UNIT_MG.get(str(row.get('DAY_MAX_DOSG_QY_UNIT', '')).strip().lower())
    if factor is None or not value.is_finite() or value <= 0:
        return None
    return value * factor


def pick_limit(rows, ingredient_code='', ingredient_name=''):
    """먹는 약(경구) 행만 사용. 제형별 값이 다르면 가장 낮은 값으로 보수적으로 비교합니다."""
    code, name = str(ingredient_code or '').strip(), normalize_name(ingredient_name)
    same = [r for r in rows if (code and str(r.get('CPNT_CD', '')).strip() == code)
            or (name and normalize_name(r.get('DRUG_CPNT_KOR_NM')) == name)]
    oral = [r for r in same if '경구' in str(r.get('DOSAGE_ROUTE_CODE', ''))]
    values = [(v, r) for r in oral if (v := _limit_mg(r)) is not None]
    if not values:
        return None
    low, row = min(values, key=lambda x: x[0])
    distinct = sorted({str(v.normalize()) for v, _ in values}, key=Decimal)
    note = '1일 최대투여량(경구)'
    if len(distinct) > 1:
        note += f' · 제형별 기준 {", ".join(distinct)}mg 중 가장 낮은 값'
    return {'limit_mg': low, 'note': note, 'form': str(row.get('FOML_NM', '')),
            'source': 'https://www.data.go.kr/data/15098095/openapi.do'}


class DayMaxClient:
    def __init__(self, client=None):
        self.client = client or MFDSClient()
        self.cache = {}
        self.lock = threading.Lock()

    def lookup(self, ingredient_code='', ingredient_name=''):
        key = (ingredient_code or '', normalize_name(ingredient_name))
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        base = settings().get('DAYMAX_BASE_URL', '').strip() or DEFAULT_BASE
        rows = []
        if ingredient_code and re.fullmatch(r'[A-Za-z0-9]+', ingredient_code):
            rows = self.client.query(OPERATION, base=base, numOfRows=100, CPNT_CD=ingredient_code)['items']
        if not rows and ingredient_name:
            rows = self.client.query(OPERATION, base=base, numOfRows=100, DRUG_CPNT_KOR_NM=ingredient_name)['items']
        result = pick_limit(rows, ingredient_code, ingredient_name)
        with self.lock:
            self.cache[key] = result
        return result
