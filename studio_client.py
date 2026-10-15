"""Studio Extract 우선 선택, 설정 버전 정책, 업로드 파일 정리."""
import json
import logging
import re
import time
import uuid
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from config import settings
from mfds_client import ServiceError

BASE = 'https://api.upstage.ai/v2'
LOG = logging.getLogger(__name__)


def build_job_payload(agent, file_id, config_id=None):
    payload = {'model': agent, 'include': ['all'], 'input': [
        {'role': 'user', 'content': [{'type': 'input_file', 'file_id': file_id}]}]}
    if config_id is not None and str(config_id).strip():
        payload['config_id'] = str(config_id).strip()
    return payload


def _request(path, key, payload=None, content_type='application/json', method=None):
    request = Request(BASE + path, data=payload,
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': content_type}, method=method)
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ServiceError('Studio 결과 크기를 초과했습니다. 확인 필요.')
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        raise ServiceError(f'Upstage HTTP {error.code}: 키·접근 권한·사용 한도를 확인하세요.') from None
    except (URLError, TimeoutError):
        raise ServiceError('Upstage에 연결하지 못했습니다. 확인 필요.') from None
    except (ValueError, TypeError, UnicodeError):
        raise ServiceError('Upstage 응답 형식을 확인할 수 없습니다.') from None


def _step_text(step):
    if not isinstance(step, dict):
        return ''
    parts = step.get('content')
    if not isinstance(parts, list):
        return ''
    return '\n'.join(p['text'] for p in parts if isinstance(p, dict)
                     and p.get('type') == 'output_text' and isinstance(p.get('text'), str))


def _parse_json(text):
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', text, re.I)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def select_output(job):
    outputs = job.get('output')
    outputs = outputs if isinstance(outputs, list) else []
    extracts = [s for s in outputs if isinstance(s, dict) and 'extract' in str(s.get('model', '')).lower()]
    for step in reversed(extracts):
        text = _step_text(step)
        parsed = _parse_json(text)
        if parsed is not None:
            return text, parsed, str(step.get('model')), []
    warnings = ['Extract 결과를 JSON으로 읽지 못해 마지막 출력으로 대체했습니다. 확인 필요.'] if extracts else []
    step = outputs[-1] if outputs else {}
    text = _step_text(step) or (job.get('output_text') if isinstance(job.get('output_text'), str) else '')
    return text, _parse_json(text), str(step.get('model', 'last')) if isinstance(step, dict) else 'last', warnings


def analyze_photo(content, mime):
    config = settings()
    key, agent = config.get('UPSTAGE_API_KEY', ''), config.get('UPSTAGE_AGENT_ID', '')
    config_id = config.get('UPSTAGE_CONFIG_ID', '').strip()
    if not key or not agent.startswith('agt_'):
        raise ServiceError('.env에 UPSTAGE_API_KEY와 UPSTAGE_AGENT_ID를 입력하세요.')
    extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}.get(mime)
    if not extension or not content or len(content) > 10 * 1024 * 1024:
        raise ServiceError('JPG·PNG·WEBP 사진 한 장(10MB 이하)을 선택하세요.')
    boundary = 'Learningflow' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nuser_data\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="photo.{extension}"\r\n'
            f'Content-Type: {mime}\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    file_id, result = None, None
    try:
        uploaded = _request('/files', key, body, 'multipart/form-data; boundary=' + boundary)
        file_id = uploaded.get('id') if isinstance(uploaded, dict) else None
        if not file_id or not isinstance(file_id, str):
            raise ServiceError('Upstage 파일 ID를 받지 못했습니다.')
        payload = build_job_payload(agent, file_id, config_id)
        job = _request('/responses', key, json.dumps(payload).encode())
        if not isinstance(job, dict) or not job.get('id'):
            raise ServiceError('Upstage 작업 ID를 받지 못했습니다.')
        deadline = time.monotonic() + 240
        while job.get('status') in ('queued', 'in_progress'):
            if time.monotonic() >= deadline:
                raise ServiceError('분석 대기 시간이 초과되었습니다. Studio 작업 상태를 확인하세요.')
            time.sleep(2)
            job = _request('/responses/' + quote(str(job['id']), safe='') + '?include=all', key)
        if job.get('status') != 'completed':
            raise ServiceError('Studio 작업이 완료되지 않았습니다. 실패 노드를 확인하세요.')
        text, extracted, selected, warnings = select_output(job)
        result = {'job_id': job['id'], 'agent_id': agent,
                  'config_policy': 'pinned' if config_id else 'latest',
                  'requested_config_id': config_id or None, 'config_id': job.get('config_id'),
                  'selected_model': selected, 'text': text, 'extracted': extracted, 'warnings': warnings}
        return result
    finally:
        if file_id:
            try:
                deleted = _request('/files/' + quote(file_id, safe=''), key, method='DELETE')
                if isinstance(deleted, dict) and deleted.get('deleted') is False:
                    raise ServiceError('파일 삭제 미완료')
                if result is not None:
                    result['file_cleanup'] = 'deleted'
            except Exception:
                message = 'Upstage 업로드 파일 삭제를 완료하지 못했습니다. Studio에서 파일 보관 상태를 확인하세요.'
                LOG.warning(message)
                if result is not None:
                    result['warnings'].append(message)
                    result['file_cleanup'] = 'warning'
