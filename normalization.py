"""표기만 정리합니다. 염형·동의어·다른 화학명은 변환하지 않습니다."""
import re
import unicodedata

STANDARD = re.compile(r'^(?:kp|usp(?:-?nf)?|ep|bp|jp|ph\.?eur\.?|별규|약전|규격)[\s.\-\d]*$', re.I)
# 회사명 앞뒤의 법인 표기. NFKC 후 ㈜는 (주)가 됩니다.
COMPANY_FORM = re.compile(r'\((?:주|유|재|사|합|株)\)|주식회사|유한회사|유한책임회사|재단법인|사단법인|co\.?,?\s*ltd\.?|inc\.?|corp\.?|corporation|limited', re.I)


def normalize_name(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKC', value).lower()
    value = re.sub(r'\(([^()]*)\)', lambda m: '' if STANDARD.fullmatch(m.group(1).strip()) else m.group(0), value)
    return re.sub(r'\s+', '', value)


def normalize_company(value):
    """'(주)종근당', '㈜종근당', '주식회사 종근당', '종근당'을 같은 회사로 봅니다."""
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKC', value).lower()
    value = COMPANY_FORM.sub('', value)
    return re.sub(r'[\s.,]+', '', value)
