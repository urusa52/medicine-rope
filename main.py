"""Python 표준 라이브러리만으로 실행하는 로컬 웹 앱: python main.py"""
import argparse
import base64
import binascii
import hmac
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from config import ROOT, settings
from mfds_client import MFDSClient, ServiceError
from studio_client import analyze_photo
from calculator import calculate
from workflow import AnalysisWorkflow

TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOST = ''
STATIC = ROOT / 'static'
WORKFLOW = AnalysisWorkflow()
DEMO = False   # demo_fake.py에서만 True (가짜 API)


def dispatch_post(path, data):
    """HTTP 처리와 분리해 새 경로도 외부 API 없이 테스트할 수 있게 합니다."""
    client = MFDSClient()
    if path == '/api/search':
        page = int(data.get('page', 1))
        if not 1 <= page <= 1000:
            raise ServiceError('페이지 범위를 확인하세요.')
        return client.search(data.get('name', ''), page)
    if path == '/api/detail':
        return client.detail(data.get('code', ''))
    if path in ('/api/studio', '/api/analyze-and-match'):
        if data.get('consent') is not True:
            raise ServiceError('사진의 Upstage 전송에 동의해야 분석할 수 있습니다.')
        try:
            content = base64.b64decode(data.get('base64', ''), validate=True)
        except (binascii.Error, TypeError):
            raise ServiceError('사진 데이터 형식이 잘못되었습니다.') from None
        if not content or len(content) > 10 * 1024 * 1024:
            raise ServiceError('사진 한 장은 10MB 이하로 입력하세요.')
        return (WORKFLOW.analyze_and_match(content, data.get('mime', ''))
                if path == '/api/analyze-and-match' else analyze_photo(content, data.get('mime', '')))
    if path == '/api/match-search':
        page = int(data.get('page', 1))
        if not 1 <= page <= 1000:
            raise ServiceError('페이지 범위를 확인하세요.')
        return WORKFLOW.search_again(data.get('photo_id'), data.get('name', ''), page)
    if path == '/api/import-and-match':
        extracted = data.get('extracted')
        if not isinstance(extracted, dict):
            raise ServiceError('Extract JSON 객체가 필요합니다.')
        return WORKFLOW.match_extracted(extracted)
    if path == '/api/confirm-match':
        return WORKFLOW.confirm_candidate(data.get('candidate_id'), data.get('confirmed'), data.get('mismatch_ack'))
    if path == '/api/evaluate':
        rules = json.loads((ROOT / 'verified_rules.json').read_text(encoding='utf-8'))
        return WORKFLOW.evaluate(data.get('products'), data.get('mode', 'single'), rules,
                                 check_safety=data.get('check_safety') is True)
    if path == '/api/demo-evaluate':
        # 가상 예제는 별도 경로에서 서버의 고정 데이터만 사용합니다.
        samples = json.loads((STATIC / 'sample.json').read_text(encoding='utf-8'))
        quantities = data.get('quantities')
        if not isinstance(quantities, list) or len(quantities) != len(samples):
            raise ServiceError('예제 수량을 확인하세요.')
        for sample, quantity in zip(samples, quantities):
            sample['quantity'] = quantity
        result = calculate(samples, data.get('mode', 'single'), [])
        result['demo'] = True
        return result
    raise ServiceError('지원하지 않는 요청입니다.')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # API 키, 제품 검색어, 사진 내용 등이 요청 로그에 남지 않도록 합니다.
        pass

    def send(self, status, payload, mime='application/json; charset=utf-8'):
        raw = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, (dict, list)) else payload
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        return self.headers.get('Host') == ALLOWED_HOST

    def do_GET(self):
        if not self.valid_host():
            self.send(403, {'error': '허용되지 않은 호스트입니다.'})
            return
        path = urlsplit(self.path).path
        if path == '/api/status':
            conf = settings()
            self.send(200, {'mfds_ready': bool(conf.get('MFDS_SERVICE_KEY')),
                            'studio_ready': bool(conf.get('UPSTAGE_API_KEY')),
                            'agent_id': conf.get('UPSTAGE_AGENT_ID', ''),
                            'config_policy': 'pinned' if conf.get('UPSTAGE_CONFIG_ID', '').strip() else 'latest',
                            'requested_config_id': conf.get('UPSTAGE_CONFIG_ID', '').strip() or None, 'token': TOKEN, 'demo': DEMO})
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/sample.json': ('sample.json', 'application/json; charset=utf-8')}
        if path not in assets:
            self.send(404, {'error': '페이지를 찾을 수 없습니다.'})
            return
        filename, mime = assets[path]
        self.send(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        if (not self.valid_host() or self.headers.get('Origin') != 'http://' + ALLOWED_HOST
                or not hmac.compare_digest(self.headers.get('X-App-Token', ''), TOKEN)):
            self.send(403, {'error': '이 앱 화면에서 요청해 주세요.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 15 * 1024 * 1024:
                raise ServiceError('요청 크기가 허용 범위를 벗어났습니다.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ServiceError('요청 형식을 확인하세요.')
            path = urlsplit(self.path).path
            result = dispatch_post(path, data)
            self.send(200, result)
        except ServiceError as error:
            self.send(400, {'error': str(error)})
        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
            self.send(400, {'error': '입력 데이터 또는 로컬 기준 파일 형식을 확인하세요.'})
        except Exception:
            self.send(500, {'error': '처리 중 오류가 발생했습니다. 인증키를 제외한 입력 형식을 확인해 주세요.'})


def run():
    global ALLOWED_HOST
    parser = argparse.ArgumentParser(description='러닝플로우 약품 성분 확인')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    port = int(settings().get('PORT', '8767'))
    ALLOWED_HOST = f'127.0.0.1:{port}'
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        raise SystemExit('앱을 열 수 없습니다. 다른 실행 창을 닫거나 .env의 PORT를 변경하세요.') from None
    print(f'러닝플로우 실행: http://{ALLOWED_HOST}')
    print('키는 .env에 입력하세요. 종료: Ctrl+C. 현재 PC에서만 접속할 수 있습니다.')
    if not args.no_browser:
        webbrowser.open('http://' + ALLOWED_HOST)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    run()
