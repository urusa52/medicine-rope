"""python diagnose_mfds.py: 키를 출력하지 않고 설정 검사 및 공식 API 1회 조회."""
import os
from urllib.parse import urlsplit, unquote
from config import ROOT, settings
from mfds_client import MFDSClient, ServiceError


def run():
    conf = settings()
    key = conf.get('MFDS_SERVICE_KEY', '')
    print('설정 파일: 프로젝트 폴더의 .env')
    print('키 입력:', '있음' if key else '없음')
    print('인증키 출처: 수정본 프로젝트의 .env만 사용')
    print('키 형태:', 'Upstage 키로 보임 — 잘못된 키' if key.startswith('up_') else '공공데이터 키 형식 확인 필요' if not key else '입력됨 (실제 유효성은 서버에서 확인)')
    print('인코딩 키 처리:', 'URL 디코딩 후 한 번 인코딩' if unquote(key) != key else '요청변수로 한 번 인코딩')
    print('키 내부 공백:', '있음 — 제거 필요' if any(c.isspace() for c in key) else '없음')
    base = conf.get('MFDS_BASE_URL', '')
    parsed = urlsplit(base)
    print('공식 Service08 주소:', '일치' if parsed.hostname == 'apis.data.go.kr' and parsed.path.rstrip('/') == '/1471000/DrugPrdtPrmsnInfoService08' else '확인 필요')
    print('공식 제품 검색 1회 확인 중…')
    try:
        result = MFDSClient().query('getDrugPrdtPrmsnInq08', item_name='타이레놀', numOfRows=1)
        print('조회 성공 / 검색 결과:', result['total'], '건')
        return 0
    except ServiceError as error:
        print('진단 결과:', str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
