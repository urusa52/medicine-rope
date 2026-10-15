"""작업 폴더에 상관없이 이 프로젝트의 .env를 읽습니다. 비밀값은 출력하지 않습니다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def settings():
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    return values
