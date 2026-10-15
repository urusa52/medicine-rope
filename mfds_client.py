"""식약처 공식 Service08 API. URL/키/개인정보를 오류 메시지에 노출하지 않습니다."""
import json
import socket
import ssl
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree as ET

from config import settings

SOURCE = 'https://www.data.go.kr/data/15095677/openapi.do'


class ServiceError(Exception):
    pass


ERROR_HINTS = {
    '20': '이 API에 대한 인증 또는 접근 권한이 없습니다. 공공데이터포털에서 ‘의약품 제품 허가정보’ 활용신청 승인 상태를 확인하세요.',
    '22': '일일 호출 한도를 초과했습니다. 활용신청 상세의 사용량을 확인하세요.',
    '23': '초당 호출 한도를 초과했습니다. 잠시 후 다시 시도하세요.',
    '29': '요청 IP가 차단되었습니다. 제공기관에 해제를 문의하세요.',
    '30': '등록되지 않은 인증키입니다. 같은 계정의 일반 인증키(Decoding)와 활용신청을 확인하세요.',
    '31': '인증키 사용 기간이 만료되었습니다. 활용신청 이용 기간을 확인하세요.',
    '12': 'API 주소 또는 서비스 버전을 확인해야 합니다. 활용신청 화면의 요청주소와 비교하세요.',
    '10': '요청변수 형식이 잘못되었습니다. API 명세를 확인하세요.',
}


def gateway_error(raw):
    """게이트웨이 본문에서 알려진 오류 코드만 추출. 원문/URL/키를 반환하지 않음."""
    text = raw.decode('utf-8-sig', errors='replace')
    code = None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            envelope = obj.get('OpenAPI_ServiceResponse', obj.get('response', obj))
            if isinstance(envelope, dict):
                header = envelope.get('cmmMsgHeader', envelope.get('header', envelope))
                if isinstance(header, dict):
                    code = header.get('returnReasonCode', header.get('resultCode'))
    except (ValueError, TypeError):
        try:
            root = ET.fromstring(text)
            code = root.findtext('.//returnReasonCode') or root.findtext('.//resultCode')
        except ET.ParseError:
            pass
    code = str(code).strip() if code is not None else ''
    if code in ERROR_HINTS:
        return f'공공데이터 오류 {code}: {ERROR_HINTS[code]}'
    # 서버가 JSON/XML 대신 알려진 오류명만 보내는 경우도 구분합니다.
    names = {'SERVICE_KEY_IS_NOT_REGISTERED_ERROR': '30', 'SERVICE_ACCESS_DENIED_ERROR': '20',
             'LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR': '22',
             'LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR': '23',
             'DEADLINE_HAS_EXPIRED_ERROR': '31', 'NO_OPENAPI_SERVICE_ERROR': '12'}
    for name, mapped in names.items():
        if name in text:
            return f'공공데이터 오류 {mapped}: {ERROR_HINTS[mapped]}'
    return None


def decode_response(raw):
    text = raw.decode('utf-8-sig')
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise ServiceError('API에서 읽을 수 없는 응답을 받았습니다. 점검 중인지 확인해 주세요.') from None
        auth = root.find('.//returnReasonCode')
        if auth is not None:
            raise ServiceError(gateway_error(raw) or 'API 인증 또는 권한 오류입니다. 키·활용신청 승인·유효기간을 확인하세요.')
        header = root.find('.//header')
        body = root.find('.//body')
        data = {'header': {e.tag: e.text or '' for e in header} if header is not None else {},
                'body': {'items': [{e.tag: ''.join(e.itertext()) for e in item}
                                   for item in root.findall('.//items/item')],
                         'totalCount': body.findtext('totalCount', '0') if body is not None else 0}}
    if not isinstance(data, dict):
        raise ServiceError('API 응답 구조가 예상과 다릅니다.')
    if 'OpenAPI_ServiceResponse' in data:
        raise ServiceError(gateway_error(raw) or 'API 인증 또는 접근 권한 오류입니다. 활용신청과 인증키를 확인하세요.')
    response = data.get('response', data)
    header = response.get('header', {})
    code = str(header.get('resultCode', ''))
    if code not in ('00', '0', '0000', 'NORMAL_SERVICE'):
        raise ServiceError(gateway_error(raw) or 'API 요청을 처리하지 못했습니다. 명세와 승인 상태를 확인하세요.')
    body = response.get('body') or {}
    items = body.get('items') or []
    if isinstance(items, dict):
        items = items.get('item', items)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ServiceError('API 품목 목록 구조를 확인할 수 없습니다.')
    try:
        total = int(body.get('totalCount', len(items)))
    except (ValueError, TypeError):
        total = len(items)
    return {'items': items, 'total': total, 'source': SOURCE}


class MFDSClient:
    def __init__(self, transport=None):
        self.transport = transport or urlopen

    def query(self, operation, base=None, **params):
        config = settings()
        key = config.get('MFDS_SERVICE_KEY', '').strip()
        if not key:
            raise ServiceError('.env의 MFDS_SERVICE_KEY에 일반 인증키(Decoding)를 입력해 주세요.')
        if key.startswith('up_'):
            raise ServiceError('MFDS_SERVICE_KEY에 Upstage 키가 입력되어 있습니다. 공공데이터포털의 일반 인증키를 입력하세요.')
        if any(c.isspace() for c in key):
            raise ServiceError('식약처 인증키에 공백 또는 줄바꿈이 있습니다. 키만 한 줄로 입력하세요.')
        base = base or config.get('MFDS_BASE_URL', 'https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08')
        if not base.startswith('https://apis.data.go.kr/1471000/'):
            raise ServiceError('식약처 API 주소는 공식 HTTPS 주소(apis.data.go.kr/1471000/)여야 합니다.')
        url = base.rstrip('/') + '/' + operation + '?' + urlencode({
            'serviceKey': unquote(key), 'type': 'json', 'numOfRows': 50, 'pageNo': 1, **params})
        try:
            with self.transport(Request(url, headers={'Accept': 'application/json'}), timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ServiceError('API 응답이 너무 큽니다. 검색어를 더 구체적으로 입력하세요.')
            return decode_response(raw)
        except HTTPError as error:
            try:
                reason = gateway_error(error.read(65536))
            except (OSError, ValueError):
                reason = None
            if reason:
                raise ServiceError(f'식약처 API HTTP {error.code} / {reason}') from None
            if error.code == 403:
                raise ServiceError('식약처 API HTTP 403: 서버가 접근을 거부했으며 세부 오류 코드는 제공하지 않았습니다. '
                                   '호출 한도 초과로 단정할 수 없습니다. 해당 API의 활용신청 승인·일반 인증키·요청주소를 확인하세요.') from None
            raise ServiceError(f'식약처 API HTTP {error.code}: API 접근 또는 서버 상태를 확인하세요.') from None
        except (URLError, TimeoutError, socket.timeout, ssl.SSLError):
            raise ServiceError('식약처 API에 연결하지 못했습니다. 인터넷 연결 또는 API 점검 상태를 확인하세요.') from None

    def search(self, name, page=1, manufacturer=None, page_size=50):
        if not isinstance(name, str) or not name.strip() or len(name) > 150:
            raise ServiceError('제품명을 1~150자로 입력하세요.')
        params = {'item_name': name.strip(), 'pageNo': page, 'numOfRows': page_size}
        if isinstance(manufacturer, str) and manufacturer.strip():
            params['entp_name'] = manufacturer.strip()
        return self.query('getDrugPrdtPrmsnInq08', **params)

    def detail(self, code):
        if not isinstance(code, str) or not code.isdigit() or len(code) > 20:
            raise ServiceError('검색 결과에서 올바른 품목기준코드를 선택하세요.')
        detail = self.query('getDrugPrdtPrmsnDtlInq08', item_seq=code)
        matched = [i for i in detail['items'] if str(i.get('ITEM_SEQ', '')) == code]
        if len(matched) != 1:
            raise ServiceError('선택한 품목의 상세정보를 정확히 확인하지 못했습니다.')
        # 공식 명세에서 주성분 조회의 파라미터는 Item_seq(대소문자 주의).
        warnings, ingredient_rows = [], []
        try:
            components = self.query('getDrugPrdtMcpnDtlInq08', Item_seq=code)
            if components['total'] > len(components['items']):
                warnings.append('주성분 응답이 일부만 반환되어 완전한 성분 목록을 확인할 수 없습니다.')
            ingredient_rows = [i for i in components['items'] if str(i.get('ITEM_SEQ', '')) == code]
            if not ingredient_rows:
                warnings.append('주성분 상세 조회 결과가 없습니다. 제품 상세 원문을 확인하세요.')
        except ServiceError as error:
            warnings.append(str(error))
        return {'product': matched[0], 'ingredients_raw': ingredient_rows,
                'warnings': warnings, 'source': SOURCE}
