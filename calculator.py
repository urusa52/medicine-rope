"""확인된 입력만 계산합니다. 성분명 유사도 매칭 및 최대량 추정은 하지 않습니다."""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from mfds_client import ServiceError
from normalization import normalize_name

FACTORS = {'mg': Decimal('1'), 'g': Decimal('1000'), 'μg': Decimal('0.001'),
           'µg': Decimal('0.001'), 'ug': Decimal('0.001'), 'mcg': Decimal('0.001')}


def number(value, label, allow_zero=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ServiceError(label + ': 숫자를 입력하세요.') from None
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0) or result > 1000000:
        raise ServiceError(label + ': 유효한 범위의 양수를 입력하세요.')
    return result


def to_mg(value, unit):
    """대조와 계산에서 공통 Decimal 환산 로직 사용."""
    if unit not in FACTORS:
        raise ServiceError('지원하지 않는 함량 단위입니다. 확인 필요.')
    return number(value, '함량') * FACTORS[unit]


def calculate(products, mode='single', rules=None):
    if mode not in ('single', 'day'):
        raise ServiceError('1회 가정 또는 1일 가정을 선택하세요.')
    if not isinstance(products, list) or not products or len(products) > 30:
        raise ServiceError('계산할 제품을 1~30개 추가하세요.')
    totals, seen, exclusions = {}, set(), []
    for product in products:
        if not isinstance(product, dict):
            raise ServiceError('제품 데이터 형식이 잘못되었습니다.')
        name = str(product.get('name', '')).strip()
        code = str(product.get('code', '')).strip()
        if not name or not code or code in seen:
            raise ServiceError('제품명·품목코드를 확인하고 같은 제품을 여러 번 추가하지 마세요.')
        seen.add(code)
        if product.get('confirmed') is not True:
            raise ServiceError(name + ': 공식 원문과 함량 기준 확인이 필요합니다.')
        role = product.get('role') if product.get('role') in ('taken', 'new') else 'new'
        quantity = number(product.get('quantity'), name + ' 가정 수량', allow_zero=True)
        if quantity == 0:
            exclusions.append(name + ': 가정 수량 0으로 제외')
            continue
        basis_unit = product.get('quantity_unit')
        ingredients = product.get('ingredients')
        if basis_unit not in ('정', '캡슐', 'mL', '포') or not isinstance(ingredients, list) or not ingredients:
            raise ServiceError(name + ': 성분과 수량 단위를 확인하세요.')
        ingredient_seen = set()
        for ingredient in ingredients:
            if not isinstance(ingredient, dict):
                raise ServiceError(name + ': 성분 형식이 잘못되었습니다.')
            component = str(ingredient.get('name', '')).strip()
            mapping_code = str(ingredient.get('ingredient_code', '')).strip()
            unit = ingredient.get('unit')
            evidence = str(ingredient.get('evidence', '')).strip()
            if not component or not evidence or unit not in FACTORS or ingredient.get('basis_unit') != basis_unit:
                raise ServiceError(name + ': 성분명·지원 단위·함량 기준·원문 근거를 확인하세요.')
            # 코드가 있으면 코드로, 없으면 정확한 표기명으로만 묶고 일치 수준을 별도 표시합니다.
            key = 'code:' + mapping_code if mapping_code else 'name:' + normalize_name(component)
            if key in ingredient_seen:
                raise ServiceError(name + ': 동일 성분 행이 반복됩니다. 중복 행을 확인하세요.')
            ingredient_seen.add(key)
            amount = number(ingredient.get('amount'), component + ' 함량')
            basis = number(ingredient.get('basis_quantity'), component + ' 기준 수량')
            dose = to_mg(amount, unit) * quantity / basis
            row = totals.setdefault(key, {'ingredient': component, 'ingredient_code': mapping_code,
                                         'matching': '공식 성분코드' if mapping_code else '이름 기준으로 묶음',
                                         'total': Decimal('0'), 'taken': Decimal('0'), 'new': Decimal('0'),
                                         'products': [], 'contributions': []})
            row['total'] += dose
            row[role] += dose
            row['products'].append(code)
            row['contributions'].append({'product': name, 'product_code': code, 'mg': str(dose),
                                         'evidence': evidence, 'source': product.get('source', ''),
                                         'quantity': str(quantity), 'quantity_unit': basis_unit, 'role': role})
    if not totals:
        raise ServiceError('0보다 큰 가정 수량을 하나 이상 입력하세요.')
    output = []
    for row in totals.values():
        row['total_mg'] = str(row.pop('total'))
        row['taken_mg'], row['new_mg'] = str(row.pop('taken')), str(row.pop('new'))
        row['duplicate'] = len(row['products']) > 1
        # 기준은 서버 로컬의 검토 완료 규칙에서만 가져오며 클라이언트 최대량은 받지 않습니다.
        candidates = [r for r in (rules or []) if r.get('reviewed') is True
                      and ((r.get('ingredient_code') and r.get('ingredient_code') == row['ingredient_code'])
                           or (r.get('ingredient_name') and normalize_name(r['ingredient_name']) == normalize_name(row['ingredient'])))
                      and r.get('mode') == mode and r.get('population') == 'adult'
                      and r.get('reviewer') and r.get('reviewed_at') and r.get('source_url')
                      and (not r.get('product_codes') or
                           set(row['products']).issubset(set(r['product_codes'])))]
        row['criterion_status'] = 'unknown'
        row['criterion_note'] = '적용 가능한 검증 기준 없음. 초과 여부 미판정.'
        if len(candidates) == 1:
            rule = candidates[0]
            limit = number(rule.get('limit_mg'), '검증 기준')
            row.update(criterion_status='exceeded' if Decimal(row['total_mg']) > limit else 'not_exceeded',
                       limit_mg=str(limit), rule_source=rule['source_url'], rule_id=rule.get('id', ''), criterion_origin='local_rule',
                       criterion_note='등록된 성인 가정 시나리오의 용량 기준만 비교. 복용 허가 아님.')
        elif len(candidates) > 1:
            row['criterion_note'] = '적용 기준이 여러 개입니다. 기준 충돌 확인 필요.'
        output.append(row)
    return {'mode': mode, 'created_at': datetime.now(timezone.utc).isoformat(), 'ingredients': output,
            'overall': 'not_a_safety_assessment', 'excluded': exclusions,
            'unassessed': ['실제 복용 이력·시간·간격', '병용금기 및 효능군 중복의 자동 조회',
                           '개인 질환·임신·간·신장 기능', '성분코드가 없는 성분의 동의어·염형 차이'],
            'notice': '가정 수량에 따른 계산입니다. 최대량 미초과는 추가 복용 가능 또는 병용 안전을 의미하지 않습니다.'}
