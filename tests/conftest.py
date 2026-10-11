import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import make_fixtures  # noqa: E402

make_fixtures.main()  # 픽스처가 없으면 생성 (대용량 파일은 .gitignore 대상)

from fastapi.testclient import TestClient  # noqa: E402

from backend.errors import ERRORS  # noqa: E402
from backend.main import app  # noqa: E402

FIXTURES = make_fixtures.FIXTURES
PRIMARY = "test/primary-model"
FALLBACK = "test/fallback-model"
FAKE_KEY = "sk-or-v1-TESTKEY-0123456789abcdef"


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def fake_env(monkeypatch):
    """모킹 테스트용: 가짜 키와 테스트 모델 ID (실제 .env 값을 덮어쓴다)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", PRIMARY)
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", FALLBACK)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def assert_error(resp, code: str):
    """§2.2 통일 에러 형식 + §4 상태·메시지 정확 일치 (AC-U-16)."""
    status, message = ERRORS[code]
    body = resp.json()
    assert resp.status_code == status, (resp.status_code, body)
    assert list(body.keys()) == ["error"], body
    assert body["error"]["code"] == code, body
    assert body["error"]["message"] == message, body
    assert resp.headers["content-type"].startswith("application/json")
