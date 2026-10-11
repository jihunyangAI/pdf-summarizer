"""실제 OpenRouter 호출 테스트 (비용 발생). 실행: python -m pytest -m live -s

.env의 OPENROUTER_API_KEY / OPENROUTER_MODEL 을 그대로 사용한다(값은 출력하지 않음).
"""
import os
import re
import time

import pytest

from backend.summarizer import TOTAL_BUDGET
from tests.conftest import assert_error, fixture_bytes

pytestmark = pytest.mark.live

HANGUL = re.compile(r"[가-힣]")
BULLET = re.compile(r"^\s*([-•*]|\d+[.)])")


@pytest.fixture(autouse=True)
def _require_key():
    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        pytest.fail("live 테스트에는 .env의 OPENROUTER_API_KEY가 필요합니다")


def _extract(client, name):
    r = client.post("/api/upload", files={"file": (name, fixture_bytes(name), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["text"]


def _check_summary(body):
    s = body["summary"]
    assert s["one_line"].strip() and s["detailed"].strip()
    assert 3 <= len(s["key_points"]) <= 7
    assert all(k.strip() and not BULLET.match(k) for k in s["key_points"])
    assert HANGUL.search(s["one_line"]) and HANGUL.search(s["detailed"])
    assert body["model"] and isinstance(body["fallback_used"], bool)
    assert isinstance(body["elapsed_ms"], int) and body["elapsed_ms"] >= 0


def _post(client, text, filename):
    t0 = time.monotonic()
    r = client.post("/api/summarize", json={"text": text, "filename": filename})
    dt = time.monotonic() - t0
    info = r.json()
    print(f"\n[live] {filename}: HTTP {r.status_code} {dt:.1f}s model={info.get('model')} "
          f"fallback={info.get('fallback_used')} chunks={info.get('chunk_count')} "
          f"err={info.get('error', {}).get('code')}")
    return r, dt


def test_live_health(client):
    h = client.get("/api/health").json()
    assert h["api_key_configured"] is True
    print(f"\n[live] models: default={h['default_model']} fallback={h['fallback_model']}")


def test_live_ac_s01_korean_short(client):
    r, _ = _post(client, _extract(client, "text_1p_ko.pdf"), "text_1p_ko.pdf")
    assert r.status_code == 200, r.text
    _check_summary(r.json())
    assert r.json()["chunk_count"] == 1


def test_live_ac_s02_english_to_korean(client):
    r, _ = _post(client, _extract(client, "text_1p_en.pdf"), "text_1p_en.pdf")
    assert r.status_code == 200, r.text
    _check_summary(r.json())


def test_live_ac_s19_long_chunked(client):
    text = _extract(client, "long_text.pdf")
    assert len(text) > 30_000
    r, dt = _post(client, text, "long_text.pdf")
    assert r.status_code == 200, r.text
    _check_summary(r.json())
    assert r.json()["chunk_count"] >= 2
    assert dt < TOTAL_BUDGET + 5


def test_live_near_limit_within_budget(client):
    """~295,000자: 성공하거나, 실패하더라도 예산(180초) 안에 AI_TIMEOUT으로 깔끔히 끝나야 한다."""
    text = _extract(client, "huge_text.pdf")[:295_000]
    r, dt = _post(client, text, "huge_text.pdf")
    assert dt < TOTAL_BUDGET + 5
    if r.status_code == 200:
        _check_summary(r.json())
        assert r.json()["chunk_count"] >= 10
    else:
        assert_error(r, "AI_TIMEOUT")
