"""대조·후보 정렬·자동 선택·확인과 '더 먹어도 되는지' 판정 경계를 담당합니다."""
import copy
import secrets
import threading
import time
from decimal import Decimal

from matcher import compare_ingredients, manufacturer_matches, official_calculation_product, text
from mfds_client import MFDSClient, ServiceError
from studio_client import analyze_photo
from calculator import calculate
from daymax_client import DayMaxClient
from dur_client import DURClient

TTL = 1800
LIMITS = {'photo': 300, 'candidate': 300, 'approved': 300}

UNASSESSED = ['마지막 복용 후 지난 시간과 복용 간격', '나이·체중·임신·간/신장 기능 등 개인 조건',
              '처방약·건강기능식품 등 목록에 넣지 않은 약', '성분코드가 없는 성분의 염형·동의어 차이']


def pick_auto(candidates):
    """성분 전체 일치 + 계산 가능 후보가 '하나로 좁혀질 때만' 자동 선택합니다."""
    ready = [c for c in candidates if c['comparison']['all_match'] and c['comparison']['calculation_ready']]
    if len(ready) == 1:
        return ready[0], '성분·함량이 모두 일치하는 제품이 하나예요.'
    maker = [c for c in ready if c['manufacturer_match']]
    if len(maker) == 1:
        return maker[0], '성분·함량과 제조사가 모두 일치해요.'
    return None, ('성분이 일치하는 제품이 여러 개예요. 직접 골라 주세요.' if ready
                  else '사진과 완전히 일치하는 제품을 찾지 못했어요. 직접 골라 주세요.')


class AnalysisWorkflow:
    def __init__(self, client=None, analyzer=None, daymax=None, dur=None):
        self.client = client or MFDSClient()
        self.analyzer = analyzer or analyze_photo
        self.daymax = daymax or DayMaxClient()
        self.dur = dur or DURClient()
        self.records = {kind: {} for kind in LIMITS}   # 종류별로 따로 보관: 검색이 많아도 확인한 약이 밀려나지 않음
        self.lock = threading.Lock()

    def _put(self, kind, value):
        with self.lock:
            now = time.monotonic()
            store = {k: v for k, v in self.records[kind].items() if now - v[0] < TTL}
            if len(store) >= LIMITS[kind]:
                del store[min(store, key=lambda k: store[k][0])]
            token = secrets.token_urlsafe(24)
            store[token] = (now, copy.deepcopy(value))
            self.records[kind] = store
            return token

    def _get(self, token, kind):
        if not isinstance(token, str):
            raise ServiceError('확인 정보가 없습니다. 후보를 다시 조회하세요.')
        with self.lock:
            record = self.records[kind].get(token)
            if not record or time.monotonic() - record[0] >= TTL:
                raise ServiceError('확인 정보가 만료되었습니다. 후보를 다시 조회하세요.')
            return copy.deepcopy(record[1])

    def analyze_and_match(self, content, mime):
        studio = self.analyzer(content, mime)
        extracted = studio.get('extracted')
        extracted = extracted if isinstance(extracted, dict) else {}
        result = self.match_extracted(extracted)
        result['studio'] = studio
        return result

    def match_extracted(self, extracted, name=None, page=1):
        extracted = extracted if isinstance(extracted, dict) else {}
        photo_id = self._put('photo', extracted)
        name = text(name) if name is not None else text(extracted.get('product_name_raw'))
        base = {'photo_extracted': extracted, 'photo_id': photo_id, 'candidates': [], 'total': 0,
                'page': page, 'warnings': [], 'auto_candidate_id': None, 'auto_reason': ''}
        if not name:
            return {**base, 'status': '제품을 찾지 못함', 'reason': '사진에서 제품명을 읽지 못했어요. 제품명으로 검색해 주세요.'}
        try:
            found = self.client.search(name, page, page_size=10)
        except ServiceError as error:
            return {**base, 'status': '확인 필요', 'reason': str(error)}
        candidates, warnings, seen = [], [], set()
        for item in found['items'][:10]:
            code = str(item.get('ITEM_SEQ', ''))
            if not code or code in seen:
                continue
            seen.add(code)
            try:
                detail = self.client.detail(code)
                comparison = compare_ingredients(extracted, detail)
            except ServiceError:
                detail = {'product': item, 'ingredients_raw': [], 'warnings': ['공식 상세 조회 실패. 확인 필요.'],
                          'source': found.get('source', '')}
                comparison = compare_ingredients(extracted, detail)
            manufacturer_match = manufacturer_matches(extracted.get('manufacturer'), detail['product'].get('ENTP_NAME'))
            candidate_id = self._put('candidate', {'detail': detail, 'comparison': comparison, 'extracted': extracted})
            candidates.append({'candidate_id': candidate_id, 'product': detail['product'],
                               'manufacturer_match': manufacturer_match,
                               'comparison': comparison, 'source': detail.get('source', '')})
        candidates.sort(key=lambda c: (not c['manufacturer_match'], not c['comparison']['all_match'],
                                       str(c['product'].get('ITEM_SEQ', ''))))
        if found['total'] > (page - 1) * 10 + len(found['items'][:10]):
            warnings.append('이 쪽의 후보 10개만 대조했어요. 원하는 제품이 없으면 다음 쪽을 보거나 제품명을 좁혀 검색하세요.')
        auto, reason = pick_auto(candidates) if candidates else (None, '')
        return {**base, 'status': '후보 확인 필요' if candidates else '제품을 찾지 못함',
                'reason': '' if candidates else '해당 제품명으로 공식 제품을 찾지 못했어요. 제품명을 바꿔 검색해 주세요.',
                'candidates': candidates, 'total': found['total'], 'warnings': warnings,
                'auto_candidate_id': auto['candidate_id'] if auto else None, 'auto_reason': reason}

    def search_again(self, photo_id, name, page=1):
        extracted = self._get(photo_id, 'photo') if photo_id else {}
        return self.match_extracted(extracted, name=name, page=page)

    def confirm_candidate(self, candidate_id, confirmed, mismatch_ack=False):
        if confirmed is not True:
            raise ServiceError('제품과 공식 성분을 확인한 후 확인 버튼을 누르세요.')
        record = self._get(candidate_id, 'candidate')
        comparison = record['comparison']
        if comparison['status'] != '일치' and mismatch_ack is not True:
            raise ServiceError('불일치·누락 항목을 다시 확인하고 추가 확인을 완료하세요.')
        product = official_calculation_product(record['detail'], comparison, record['extracted'])
        approval = self._put('approved', product)
        return {'approval_id': approval, 'product': product, 'comparison_status': comparison['status']}

    def evaluate(self, submitted, mode, rules, check_safety=False):
        if not isinstance(submitted, list) or not submitted or len(submitted) > 30:
            raise ServiceError('확인된 제품을 1~30개 추가하세요.')
        canonical = []
        for item in submitted:
            if not isinstance(item, dict):
                raise ServiceError('계산 입력을 확인하세요.')
            product = self._get(item.get('approval_id'), 'approved')
            # 클라이언트가 바꾼 성분명·함량·코드는 사용하지 않습니다.
            product['quantity'] = item.get('quantity')
            product['role'] = item.get('role') if item.get('role') in ('taken', 'new') else 'new'
            canonical.append(product)
        result = calculate(canonical, mode, rules)
        if check_safety:
            result['judgement'] = self._judge(result, canonical)
        return result

    def _judge(self, result, products):
        """하루 합계를 1일 최대투여량과 비교하고 DUR을 확인해 세 단계로 알려 줍니다. 복용 허가가 아닙니다."""
        errors, reasons = [], []
        new_codes = {p['code'] for p in products if p['role'] == 'new'}
        if not new_codes:
            raise ServiceError('새로 먹으려는 약을 하나 이상 추가하세요.')
        for row in result['ingredients']:
            if row['criterion_status'] == 'unknown':
                try:
                    found = self.daymax.lookup(row['ingredient_code'], row['ingredient'])
                except ServiceError as error:
                    found = None
                    errors.append(f"1일 최대투여량 조회 실패({row['ingredient']}): {error}")
                if found:
                    limit = found['limit_mg']
                    row.update(limit_mg=str(limit), criterion_origin='daymax_api', rule_source=found['source'],
                               criterion_status='exceeded' if Decimal(row['total_mg']) > limit else 'not_exceeded',
                               criterion_note=found['note'])
                else:
                    row['criterion_note'] = '1일 최대투여량 기준을 찾지 못했어요.'
            if row['criterion_status'] in ('exceeded', 'not_exceeded'):
                row['percent'] = int(Decimal(row['total_mg']) * 100 / Decimal(row['limit_mg']))
        dur = self.dur.check([{'name': p['name'], 'code': p['code'], 'role': p['role']} for p in products])
        errors += dur['errors']
        rows = result['ingredients']
        exceeded = [r for r in rows if r['criterion_status'] == 'exceeded']
        unknown = [r for r in rows if r['criterion_status'] == 'unknown' and Decimal(r['new_mg']) > 0]
        danger = [f for f in dur['findings'] if f['level'] == 'danger']
        if exceeded or danger:
            level, title = 'stop', '지금은 더 먹지 마세요'
            reasons += [f"{r['ingredient']} 하루 합계 {r['total_mg']}mg가 최대 {r['limit_mg']}mg를 넘어요." for r in exceeded]
            reasons += [f"{f['type']}: {' + '.join(f['products'])}" for f in danger]
            message = '약사나 의사와 먼저 상담하세요.'
        elif unknown or errors or dur['findings'] or any(r['duplicate'] for r in rows):
            level, title = 'check', '약사에게 확인이 필요해요'
            reasons += [f"{r['ingredient']}: 하루 최대량 기준을 확인하지 못했어요." for r in unknown]
            reasons += [f"{r['ingredient']}: 기존 약과 같은 성분이 겹쳐요."
                        for r in rows if r['duplicate'] and r['criterion_status'] != 'exceeded']
            reasons += [f"{f['type']}({' + '.join(f['products'])}): {f['text']}" for f in dur['findings']]
            reasons += errors
            message = '아래 항목은 자동으로 판단하지 못했어요.'
        else:
            level, title = 'ok', '확인한 범위에서는 문제를 찾지 못했어요'
            message = '하루 최대량과 DUR(병용금기·효능군 중복·용량주의)만 확인했어요. 아래 항목은 확인하지 않았어요.'
        return {'level': level, 'title': title, 'message': message, 'reasons': reasons,
                'dur': dur, 'errors': errors, 'unassessed': UNASSESSED}
