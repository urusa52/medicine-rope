"""식약처 DUR 품목정보 조회(병용금기·용량주의·효능군중복).

확인된 것: 서비스 DURPrdlstInfoService03, 병용금기 getUsjntTabooInfoList03, 요청변수 itemName/mixtureItemName,
응답 필드 TYPE_NAME/PROHBT_CONTENT/REMARK (공개 래퍼 소스 기준).
추정한 것: 용량주의·효능군중복 오퍼레이션 이름과 그 응답 필드. 실제 키로 응답을 확인한 뒤 OPERATIONS를 수정하세요.
조회가 실패하면 '문제 없음'으로 처리하지 않고 확인 필요로 남깁니다.
"""
from config import settings
from mfds_client import MFDSClient, ServiceError

DEFAULT_BASE = 'https://apis.data.go.kr/1471000/DURPrdlstInfoService03'
OPERATIONS = {'taboo': 'getUsjntTabooInfoList03',      # 병용금기 (확인됨)
              'capacity': 'getCpctyAtentInfoList03',   # 용량주의 (추정)
              'efficacy': 'getEfcyDplctInfoList03'}    # 효능군중복 (추정)
LABELS = {'taboo': '병용금기', 'capacity': '용량주의', 'efficacy': '효능군 중복'}
GROUP_KEYS = ('EFFECT_NAME', 'SERS_NAME', 'CLASS_NAME')   # 효능군 이름 후보 필드 (추정)


def _text(item):
    parts = [str(item.get(k, '')).strip() for k in ('TYPE_NAME', 'PROHBT_CONTENT', 'REMARK')]
    return ' / '.join(p for p in parts if p)


def _same_product(item, code, key='ITEM_SEQ'):
    """응답에 품목코드가 있으면 같은 제품만 남깁니다. 이름 검색이 다른 제품까지 잡는 것을 막기 위함."""
    value = item.get(key)
    return value in (None, '') or str(value) == str(code)


class DURClient:
    def __init__(self, client=None):
        self.client = client or MFDSClient()

    def _lookup(self, kind, **params):
        base = settings().get('DUR_BASE_URL', '').strip() or DEFAULT_BASE
        return self.client.query(OPERATIONS[kind], base=base, numOfRows=100, **params)['items']

    def check(self, products):
        """products: [{'name','code','role'}]. 새 약 기준으로 기존 약과의 관계를 확인합니다."""
        findings, errors = [], []
        new = [p for p in products if p.get('role') == 'new']
        taken = [p for p in products if p.get('role') == 'taken']
        groups = {}

        def run(kind, product, **params):
            try:
                return [i for i in self._lookup(kind, itemName=product['name'], **params) if _same_product(i, product['code'])]
            except ServiceError as error:
                errors.append(f"{LABELS[kind]} 조회 실패({product['name']}): {error}")
                return None

        for p in new:
            for t in taken:
                items = run('taboo', p, mixtureItemName=t['name'])
                for i in items or []:
                    if _same_product(i, t['code'], 'MIXTURE_ITEM_SEQ'):
                        findings.append({'type': '병용금기', 'level': 'danger', 'products': [p['name'], t['name']],
                                         'text': _text(i) or '병용금기 항목이 있습니다.'})
            for i in run('capacity', p) or []:
                findings.append({'type': '용량주의', 'level': 'caution', 'products': [p['name']],
                                 'text': _text(i) or '용량주의 대상입니다.'})
        if new and taken:
            for p in new + taken:
                items = run('efficacy', p)
                names = {str(i.get(k)).strip() for i in items or [] for k in GROUP_KEYS if i.get(k)}
                groups[p['code']] = (p, names, items or [])
            for p in new:
                _, mine, items = groups.get(p['code'], (p, set(), []))
                if items and not mine:
                    errors.append(f"효능군 중복 응답 형식을 해석하지 못했어요({p['name']}). 필드 이름 확인 필요.")
                for t in taken:
                    _, theirs, _ = groups.get(t['code'], (t, set(), []))
                    shared = mine & theirs
                    if shared:
                        findings.append({'type': '효능군 중복', 'level': 'danger', 'products': [p['name'], t['name']],
                                         'text': '같은 효능군: ' + ', '.join(sorted(shared))})
        return {'findings': findings, 'errors': errors}
