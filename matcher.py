"""사진 Extract와 공식 주성분을 대조. 추측·동의어 치환·부동소수점 허용오차 없음."""
import json
import re
from decimal import Decimal

from calculator import to_mg, number
from mfds_client import ServiceError
import unicodedata

from normalization import normalize_company, normalize_name

UNITS = {'mg': 'mg', '밀리그램': 'mg', 'g': 'g', '그램': 'g',
         'μg': 'μg', 'µg': 'μg', 'ug': 'μg', 'mcg': 'μg', '마이크로그램': 'μg'}
UNIT_PATTERN = '|'.join(sorted(map(re.escape, UNITS), key=len, reverse=True))
BASIS_UNITS = r'정|캡슐|포|밀리리터|mL|ml'
BASIS = r'(?P<basis>[0-9]+(?:\.[0-9]+)?)\s*(?P<basis_unit>' + BASIS_UNITS + r')'
STRENGTH = re.compile(r'^\s*(?:' + BASIS + r'\s*중\s*)?(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*'
                      r'(?P<unit>' + UNIT_PATTERN + r')\s*$', re.I)


def text(value):
    return value.strip() if isinstance(value, str) else ''


def _clean(value):
    """㎎→mg 같은 호환 문자와 1,000 같은 천 단위 쉼표만 정리합니다."""
    value = unicodedata.normalize('NFKC', value)
    return re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', value)


def _basis_unit(unit):
    return 'mL' if str(unit).lower() in ('ml', '밀리리터') else unit


def parse_strength(value):
    result = {'raw': text(value), 'mg': None, 'amount': None, 'unit': None,
              'basis_quantity': None, 'basis_unit': None, 'status': '함량 미확인'}
    if not isinstance(value, str) or len(value) > 300:
        return result
    match = STRENGTH.fullmatch(_clean(value))
    if not match:
        return result
    groups = match.groupdict()
    raw_unit = groups['unit'].lower()
    unit = UNITS.get(raw_unit)
    try:
        if not unit:
            return result
        mg = to_mg(groups['amount'], unit)
        basis = number(groups['basis'], '기준 수량') if groups['basis'] else None
    except ServiceError:
        return result
    result.update(mg=str(mg), amount=groups['amount'], unit=unit, status='확인됨',
                  basis_quantity=str(basis) if basis is not None else None,
                  basis_unit=_basis_unit(groups['basis_unit']) if groups['basis_unit'] else None)
    return result


def parse_basis(value):
    """명시된 1정/100mL 등만 사용. 1정(총량 700mg) 중 표현도 처리."""
    value = _clean(text(value))
    pattern = r'([0-9]+(?:\.[0-9]+)?)\s*(' + BASIS_UNITS + r')(?:\s*\([^()]*\))?\s*(?:중|당)'
    found = re.findall(pattern, value, re.I)
    if not found:
        exact = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*(' + BASIS_UNITS + r')\s*', value, re.I)
        found = [exact.groups()] if exact else []
    normalized = {(str(Decimal(q)), _basis_unit(u)) for q, u in found}
    if len(normalized) != 1:
        return None, None
    quantity, unit = normalized.pop()
    try:
        return str(number(quantity, '공식 기준 수량')), unit
    except ServiceError:
        return None, None


def photo_ingredients(extracted):
    extracted = extracted if isinstance(extracted, dict) else {}
    values = extracted.get('effective_ingredients')
    if not isinstance(values, list):
        return []
    result = []
    for row in values[:100]:
        if not isinstance(row, dict):
            continue
        name = text(row.get('ingredient_name')) or text(row.get('base_ingredient_name'))
        aliases = {normalize_name(row.get('ingredient_name')), normalize_name(row.get('base_ingredient_name'))} - {''}
        result.append({'name': name, 'aliases': sorted(aliases),
                       'strength': parse_strength(row.get('strength_per_unit')), 'raw': row})
    return result


def official_ingredients(detail):
    product = detail.get('product') if isinstance(detail.get('product'), dict) else {}
    values = detail.get('ingredients_raw')
    result = []
    for row in values if isinstance(values, list) else []:
        if not isinstance(row, dict):
            continue
        name = text(row.get('MTRAL_NM'))
        qnt, raw_unit = row.get('QNT'), text(row.get('INGD_UNIT_CD'))
        qnt = str(qnt) if isinstance(qnt, (str, int, float)) and not isinstance(qnt, bool) else ''
        strength = parse_strength(qnt + raw_unit)
        # 코드형 단위나 불명확한 분량은 원문을 임의 해석하지 않음.
        bases = [parse_basis(row.get('CPNT_CTNT_CONT')), parse_basis(product.get('TOTAL_CONTENT'))]
        known = {b for b in bases if b[0] is not None}
        basis = next(iter(known)) if len(known) == 1 else (None, None)
        result.append({'name': name, 'normalized_name': normalize_name(name),
                       'ingredient_code': text(row.get('MTRAL_CODE')),
                       'strength': strength, 'basis_quantity': basis[0], 'basis_unit': basis[1], 'raw': row})
    return result


def compare_ingredients(extracted, detail):
    photos, official = photo_ingredients(extracted), official_ingredients(detail)
    remaining = set(range(len(official)))
    rows, warnings = [], list(detail.get('warnings') or [])
    for photo in photos:
        matches = [i for i in remaining if official[i]['normalized_name']
                   and official[i]['normalized_name'] in photo['aliases']]
        if len(matches) != 1:
            rows.append({'name': photo['name'] or '성분명 미확인', 'status': '사진에만 있음',
                         'photo': photo, 'official': None})
            if len(matches) > 1:
                warnings.append('동일 이름의 공식 성분 행이 여러 개입니다. 확인 필요.')
            continue
        i = matches[0]; remaining.remove(i); target = official[i]
        a, b = photo['strength'], target['strength']
        status = '함량 미확인'
        if a['mg'] is not None and b['mg'] is not None:
            # 양쪽에 기준이 명시되면 기준 수량당 mg로 비교. 기준 단위가 다르면 비교 불가.
            if a['basis_quantity'] and target['basis_quantity']:
                if a['basis_unit'] == target['basis_unit']:
                    amg = Decimal(a['mg']) / Decimal(a['basis_quantity'])
                    bmg = Decimal(b['mg']) / Decimal(target['basis_quantity'])
                    status = '일치' if amg == bmg else '함량 불일치'
            elif ((a['basis_quantity'] and Decimal(a['basis_quantity']) != 1)
                  or (target['basis_quantity'] and Decimal(target['basis_quantity']) != 1)):
                # 어느 쪽이든 비단위 기준인데 상대 기준이 없으면 같은 분모라고 가정하지 않음.
                status = '함량 미확인'
            else:
                status = '일치' if Decimal(a['mg']) == Decimal(b['mg']) else '함량 불일치'
        rows.append({'name': target['name'], 'status': status, 'photo': photo, 'official': target})
    for i in sorted(remaining):
        rows.append({'name': official[i]['name'] or '성분명 미확인', 'status': '공식에만 있음',
                     'photo': None, 'official': official[i]})
    raw_photo_rows = extracted.get('effective_ingredients') if isinstance(extracted, dict) else None
    malformed_photo = (not isinstance(raw_photo_rows, list) or len(raw_photo_rows) > 100
                       or any(not isinstance(r, dict) for r in raw_photo_rows))
    if malformed_photo:
        warnings.append('사진의 성분 목록 형식이 불완전합니다. 확인 필요.')
    all_match = bool(rows) and all(r['status'] == '일치' for r in rows) and not malformed_photo
    calculation_ready = bool(official) and all(o['name'] and o['strength']['mg'] is not None
                                             and o['basis_quantity'] and o['basis_unit'] for o in official)
    if not calculation_ready:
        warnings.append('공식 성분·함량·함량 기준을 완전히 확인하지 못했습니다. 계산 추가 차단.')
    if len({(o['ingredient_code'] or o['normalized_name']) for o in official}) != len(official):
        calculation_ready = False
        warnings.append('공식 성분이 중복된 행으로 반환되었습니다. 계산 추가 차단.')
    if detail.get('warnings'):
        calculation_ready = False
    return {'status': '일치' if all_match else '확인 필요', 'all_match': all_match,
            'rows': rows, 'official_ingredients': official, 'calculation_ready': calculation_ready,
            'warnings': warnings}


def manufacturer_matches(photo_name, official_name):
    a, b = normalize_company(photo_name), normalize_company(official_name)
    return bool(a and b and a == b)


def official_calculation_product(detail, comparison, extracted):
    """계산값은 공식 데이터에서만 생성. 사진은 evidence 안에만 남김."""
    if not comparison.get('calculation_ready'):
        raise ServiceError('공식 함량 또는 기준 단위가 미확인입니다. 확인 필요.')
    product = detail['product']
    ingredients = []
    for o in comparison['official_ingredients']:
        ingredients.append({'name': o['name'], 'ingredient_code': o['ingredient_code'],
                            'amount': o['strength']['amount'], 'unit': o['strength']['unit'],
                            'basis_quantity': o['basis_quantity'], 'basis_unit': o['basis_unit'],
                            'evidence': json.dumps({'official': o['raw'], 'photo': extracted}, ensure_ascii=False)})
    units = {i['basis_unit'] for i in ingredients}
    if len(units) != 1:
        raise ServiceError('공식 성분의 기준 단위가 다릅니다. 확인 필요.')
    return {'name': text(product.get('ITEM_NAME')), 'code': str(product.get('ITEM_SEQ', '')),
            'quantity': '0', 'quantity_unit': next(iter(units)), 'confirmed': True,
            'source': detail.get('source', ''), 'ingredients': ingredients,
            'warning_text': text(product.get('NB_DOC_DATA')), 'directions_text': text(product.get('UD_DOC_DATA'))}
