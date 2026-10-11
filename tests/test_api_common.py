"""GET /, /api/health, 공통 에러 변환, CORS — AC-H-*, AC-C-*, AC-N-02/03."""
import os
import time
from pathlib import Path

import httpx
import pytest
import respx

from backend import main as backend_main
from tests.conftest import ROOT, assert_error, fixture_bytes

NULL_ORIGIN = {"Origin": "null"}


# ---------------------------------------------------------------- health

def test_ac_h01_health_shape(client, fake_env):
    r = client.get("/api/health")
    assert r.status_code == 200
    h = r.json()
    assert h["status"] == "ok"
    assert isinstance(h["api_key_configured"], bool)
    assert isinstance(h["default_model"], str) and h["default_model"]
    assert isinstance(h["fallback_model"], str) and h["fallback_model"]
    assert h["max_file_mb"] == 20
    assert isinstance(h["version"], str) and h["version"]


def test_ac_h02_key_configured_not_leaked(client, fake_env):
    from tests.conftest import FAKE_KEY
    r = client.get("/api/health")
    assert r.json()["api_key_configured"] is True
    assert FAKE_KEY not in r.text


def test_ac_h03_no_key(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["api_key_configured"] is False


def test_ac_h03_blank_key_counts_as_missing(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    assert client.get("/api/health").json()["api_key_configured"] is False


def test_ac_h04_health_no_external_call_and_fast(client, fake_env):
    with respx.mock(assert_all_called=False) as router:  # 외부 호출 시 respx가 예외 발생
        route = router.route().mock(side_effect=httpx.ConnectError("blocked"))
        client.get("/api/health")  # warm-up
        t0 = time.perf_counter()
        r = client.get("/api/health")
        dt = time.perf_counter() - t0
    assert r.status_code == 200
    assert route.call_count == 0
    assert dt < 0.2


# ---------------------------------------------------------------- 서빙

def test_ac_c01_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.content == (ROOT / "index_pdf.html").read_bytes()


def test_ac_c04_not_found(client):
    assert_error(client.get("/api/nope"), "NOT_FOUND")


def test_ac_c05_method_not_allowed(client):
    assert_error(client.get("/api/upload"), "METHOD_NOT_ALLOWED")
    assert_error(client.get("/api/summarize"), "METHOD_NOT_ALLOWED")


def test_unhandled_exception_is_internal_error(client, monkeypatch):
    def boom(_data):
        raise RuntimeError("secret internal path C:\\x\\y")
    monkeypatch.setattr(backend_main, "extract_text", boom)
    r = client.post("/api/upload", files={"file": ("a.pdf", fixture_bytes("text_1p_en.pdf"))}, headers=NULL_ORIGIN)
    assert_error(r, "INTERNAL_ERROR")
    assert "secret" not in r.text
    assert r.headers.get("access-control-allow-origin") in ("*", "null")


# ---------------------------------------------------------------- CORS

def test_ac_c02_preflight_null_origin(client):
    r = client.options("/api/upload", headers={
        "Origin": "null", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "null")
    assert "POST" in r.headers.get("access-control-allow-methods", "")


@pytest.mark.parametrize("method,path,kw,code", [
    ("post", "/api/upload", {"files": {"file": ("fake.pdf", b"not a pdf")}}, "INVALID_FILE_TYPE"),  # AC-C-03
    ("post", "/api/upload", {"files": {"file": ("big.pdf", b"0" * (20 * 1024 * 1024 + 1))}}, "FILE_TOO_LARGE"),
    ("post", "/api/upload", {}, "NO_FILE"),
    ("post", "/api/summarize", {"content": b"not json"}, "INVALID_REQUEST"),
    ("get", "/api/nope", {}, "NOT_FOUND"),
    ("get", "/api/upload", {}, "METHOD_NOT_ALLOWED"),
])
def test_ac_c03_error_responses_carry_cors(client, method, path, kw, code):
    r = getattr(client, method)(path, headers=NULL_ORIGIN, **kw)
    assert_error(r, code)
    assert r.headers.get("access-control-allow-origin") in ("*", "null")


def test_cors_success_response(client):
    r = client.get("/api/health", headers=NULL_ORIGIN)
    assert r.headers.get("access-control-allow-origin") in ("*", "null")


# ---------------------------------------------------------------- 비기능

def test_ac_n02_api_key_not_in_repo_files():
    """AC-N-02: 실제 키 값이 .env 외 어떤 파일에도 없음 (값은 출력하지 않는다)."""
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key or key.startswith("sk-or-v1-TESTKEY"):
        pytest.skip("실제 키가 로드되지 않음")  # 키 미설정 환경에서는 검증 대상 없음
    leaked = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.name == ".env" or p.stat().st_size > 5_000_000:
            continue
        if any(part in (".git", "__pycache__") for part in p.parts):
            continue
        try:
            if key in p.read_text(encoding="utf-8", errors="ignore"):
                leaked.append(str(p.relative_to(ROOT)))
        except OSError:
            pass
    if leaked:
        pytest.fail(f"API 키가 다음 파일에서 발견됨: {leaked}")


def test_ac_n03_korean_path(client):
    """AC-N-03: 한글 경로에서 앱 로드 및 GET / 정상 (이 저장소 경로 자체가 한글)."""
    assert Path(backend_main.__file__).resolve().parent.parent == ROOT
    assert client.get("/").status_code == 200
