"""POST /api/summarize (OpenRouter 모킹) — FEATURE_SPEC §3.4, AC-S-01 ~ AC-S-22."""
import asyncio
import json
import logging
import time
from types import SimpleNamespace

import httpx
import pytest
import respx

from backend import summarizer
from tests.conftest import FAKE_KEY, FALLBACK, PRIMARY, assert_error

URL = "https://openrouter.ai/api/v1/chat/completions"
GOOD = {"one_line": "이 문서는 전기차 시장 전망을 다룬다.",
        "key_points": ["판매량 증가", "충전 인프라 확충", "보조금 축소"],
        "detailed": "첫 문단입니다.\n\n둘째 문단입니다."}
TEXT = "본 보고서는 2026년 국내 전기차 시장의 성장 전망을 분석한다. " * 20


def chat(content, status=200):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def status(code, body=None):
    return httpx.Response(code, json=body or {"error": {"code": code, "message": "mock error"}})


def good_for(payload):
    """Map 프롬프트에는 자유 텍스트, 최종(Reduce/단일) 프롬프트에는 JSON."""
    prompt = payload["messages"][-1]["content"]
    return chat("- 부분 요약 노트") if "[문서 일부]" in prompt else chat(GOOD)


@pytest.fixture
def orouter(fake_env):
    """OpenRouter 모킹. state.handler(model, payload, n) -> Response | Exception | coroutine."""
    state = SimpleNamespace(calls=[], starts=[], handler=None, active=0, headers=[])
    with respx.mock(assert_all_called=False) as router:
        async def side_effect(request):
            payload = json.loads(request.content)
            state.calls.append(payload)
            state.starts.append(time.monotonic())
            state.headers.append(dict(request.headers))
            state.active += 1
            try:
                res = state.handler(payload["model"], payload, len(state.calls))
                if asyncio.iscoroutine(res):
                    res = await res
                if isinstance(res, Exception):
                    raise res
                return res
            finally:
                state.active -= 1
        router.post(URL).mock(side_effect=side_effect)
        yield state


def models(state):
    return [c["model"] for c in state.calls]


def post(client, text=TEXT, filename="doc.pdf"):
    return client.post("/api/summarize", json={"text": text, "filename": filename})


# ---------------------------------------------------------------- 정상

def test_ac_s01_success_shape(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    r = post(client)
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"summary", "model", "fallback_used", "elapsed_ms", "chunk_count"}
    s = b["summary"]
    assert s["one_line"] and s["detailed"]
    assert 3 <= len(s["key_points"]) <= 7
    assert b["model"] == PRIMARY
    assert b["fallback_used"] is False
    assert isinstance(b["elapsed_ms"], int) and b["elapsed_ms"] >= 0
    assert b["chunk_count"] == 1
    assert models(orouter) == [PRIMARY]


def test_request_headers_and_prompt(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    post(client, filename="보고서.pdf")
    h = orouter.headers[0]
    assert h["authorization"] == f"Bearer {FAKE_KEY}"
    assert "http-referer" in h and "x-title" in h
    prompt = orouter.calls[0]["messages"][-1]["content"]
    assert "한국어" in prompt and "one_line" in prompt and "key_points" in prompt
    assert "보고서.pdf" in prompt and TEXT.strip()[:50] in prompt


def test_filename_truncated_to_255(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    name = "가" * 300 + ".pdf"
    assert post(client, filename=name).status_code == 200
    prompt = orouter.calls[0]["messages"][-1]["content"]
    assert "가" * 255 in prompt and "가" * 256 not in prompt


def test_filename_optional_and_non_string_ignored(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    assert client.post("/api/summarize", json={"text": TEXT}).status_code == 200
    assert client.post("/api/summarize", json={"text": TEXT, "filename": 123}).status_code == 200


def test_lone_surrogates_do_not_crash(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    body = '{"text": "' + TEXT.strip().replace('"', "") + ' \\udcec", "filename": "a\\udcec.pdf"}'
    r = client.post("/api/summarize", content=body.encode("utf-8"),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    prompt = orouter.calls[0]["messages"][-1]["content"]
    assert "\udcec" not in prompt


# ---------------------------------------------------------------- 입력 검증

def test_ac_s03_empty_text(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    assert_error(post(client, "   \n "), "EMPTY_TEXT")
    assert orouter.calls == []


@pytest.mark.parametrize("kw", [
    {"json": {}}, {"json": {"text": 123}}, {"json": {"text": None}}, {"json": ["text"]},
    {"json": "text"}, {"content": b"not json", "headers": {"Content-Type": "application/json"}},
    {"content": b""}, {"content": "{\"text\": \"\xff".encode("latin-1")},
])
def test_ac_s04_invalid_request(client, orouter, kw):
    orouter.handler = lambda m, p, n: chat(GOOD)
    assert_error(client.post("/api/summarize", **kw), "INVALID_REQUEST")
    assert orouter.calls == []


def test_ac_s05_text_too_long(client, orouter):
    orouter.handler = lambda m, p, n: chat(GOOD)
    assert_error(post(client, "가" * 300_001), "TEXT_TOO_LONG")
    assert orouter.calls == []


def test_exactly_300000_chars_allowed(client, orouter):
    orouter.handler = lambda m, p, n: good_for(p)
    r = post(client, ("가나다라마바사. " * 40_000)[:300_000])
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] >= 10


def test_ac_s06_api_key_missing(client, orouter, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    orouter.handler = lambda m, p, n: chat(GOOD)
    assert_error(post(client), "API_KEY_MISSING")
    assert orouter.calls == []


# ---------------------------------------------------------------- 폴백 (§3.4.2)

@pytest.mark.parametrize("first", [
    status(429), status(500), status(502), status(503), status(404),  # AC-S-07, 08, 14
    status(400, {"error": {"code": 400, "message": "deepseek/x is not a valid model ID"}}),
    httpx.ConnectError("connection refused"),
    httpx.Response(200, json={"error": {"code": 429, "message": "Rate limit exceeded upstream"}}),
])
def test_fallback_then_success(client, orouter, first):
    orouter.handler = lambda m, p, n: first if m == PRIMARY else chat(GOOD)
    r = post(client)
    assert r.status_code == 200, r.text
    assert r.json()["fallback_used"] is True
    assert r.json()["model"] == FALLBACK
    assert models(orouter) == [PRIMARY, FALLBACK]


def test_ac_s09_primary_timeout_then_fallback(client, orouter, monkeypatch):
    monkeypatch.setattr(summarizer, "CALL_TIMEOUT", 0.5)

    async def slow():
        await asyncio.sleep(5)
        return chat(GOOD)
    orouter.handler = lambda m, p, n: slow() if m == PRIMARY else chat(GOOD)
    r = post(client)
    assert r.status_code == 200, r.text
    assert r.json()["fallback_used"] is True
    aborted_after = orouter.starts[1] - orouter.starts[0]
    assert 0.45 <= aborted_after <= 0.5 + 1.0  # 호출 타임아웃(+허용오차) 시점에 중단


def test_ac_s10_both_rate_limited(client, orouter):
    orouter.handler = lambda m, p, n: status(429)
    assert_error(post(client), "AI_RATE_LIMIT")
    assert models(orouter) == [PRIMARY, FALLBACK]


def test_ac_s11_both_timeout(client, orouter, monkeypatch):
    monkeypatch.setattr(summarizer, "CALL_TIMEOUT", 0.3)

    async def slow():
        await asyncio.sleep(5)
        return chat(GOOD)
    orouter.handler = lambda m, p, n: slow()
    assert_error(post(client), "AI_TIMEOUT")
    assert models(orouter) == [PRIMARY, FALLBACK]


def test_ac_s12_both_server_error(client, orouter):
    orouter.handler = lambda m, p, n: status(500)
    assert_error(post(client), "AI_ERROR")
    assert models(orouter) == [PRIMARY, FALLBACK]


@pytest.mark.parametrize("code", [401, 403])
def test_ac_s13_auth_error_no_fallback(client, orouter, code):
    orouter.handler = lambda m, p, n: status(code)
    assert_error(post(client), "AI_AUTH_ERROR")
    assert models(orouter) == [PRIMARY]


def test_402_credit_no_fallback(client, orouter):
    orouter.handler = lambda m, p, n: status(402)
    assert_error(post(client), "AI_ERROR")
    assert models(orouter) == [PRIMARY]


def test_fallback_failure_type_decides_error(client, orouter):
    orouter.handler = lambda m, p, n: status(503) if m == PRIMARY else status(401)
    assert_error(post(client), "AI_AUTH_ERROR")


def test_fallback_only_once_per_call(client, orouter):
    orouter.handler = lambda m, p, n: httpx.ConnectError("down")
    assert_error(post(client), "AI_ERROR")
    assert len(orouter.calls) == 2


def test_upstream_non_json_body(client, orouter):
    orouter.handler = lambda m, p, n: httpx.Response(200, text="<html>bad gateway</html>")
    assert_error(post(client), "AI_ERROR")


# ---------------------------------------------------------------- 응답 파싱 (§3.4.1)

def test_ac_s15_invalid_json_twice(client, orouter):
    orouter.handler = lambda m, p, n: chat("죄송하지만 요약할 수 없습니다.")
    assert_error(post(client), "AI_INVALID_RESPONSE")
    assert models(orouter) == [PRIMARY, PRIMARY]  # 같은 모델로 1회 재요청


def test_invalid_json_then_valid(client, orouter):
    orouter.handler = lambda m, p, n: chat("not json") if n == 1 else chat(GOOD)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["fallback_used"] is False
    assert models(orouter) == [PRIMARY, PRIMARY]


def test_empty_content_counts_as_parse_failure(client, orouter):
    orouter.handler = lambda m, p, n: chat("")
    assert_error(post(client), "AI_INVALID_RESPONSE")


def test_too_few_key_points_is_parse_failure(client, orouter):
    orouter.handler = lambda m, p, n: chat({**GOOD, "key_points": ["하나", "둘"]})
    assert_error(post(client), "AI_INVALID_RESPONSE")


@pytest.mark.parametrize("missing", ["one_line", "detailed", "key_points"])
def test_missing_field_is_parse_failure(client, orouter, missing):
    bad = {k: v for k, v in GOOD.items() if k != missing}
    orouter.handler = lambda m, p, n: chat(bad)
    assert_error(post(client), "AI_INVALID_RESPONSE")


def test_ac_s16_code_fence(client, orouter):
    fenced = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    orouter.handler = lambda m, p, n: chat(fenced)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["summary"]["one_line"] == GOOD["one_line"]


def test_ac_s17_key_points_capped_at_7(client, orouter):
    orouter.handler = lambda m, p, n: chat({**GOOD, "key_points": [f"포인트 {i}" for i in range(10)]})
    kp = post(client).json()["summary"]["key_points"]
    assert kp == [f"포인트 {i}" for i in range(7)]


def test_ac_s18_bullets_stripped(client, orouter):
    orouter.handler = lambda m, p, n: chat({**GOOD, "key_points": ["- 내용", "1. 내용", "• 내용", "* 내용", "2) 내용"]})
    kp = post(client).json()["summary"]["key_points"]
    assert kp == ["내용"] * 5


def test_html_in_summary_returned_verbatim(client, orouter):
    """서버는 문자열 그대로 반환하고, 프론트가 textContent로 표시한다(AC-F-16)."""
    xss = "<script>alert(1)</script>"
    orouter.handler = lambda m, p, n: chat({**GOOD, "one_line": xss})
    assert post(client).json()["summary"]["one_line"] == xss


# ---------------------------------------------------------------- 청크 (§3.4.3)

LONG = "\n\n".join(f"{i}번째 문단입니다. " + "전기차 시장 분석 내용이 이어집니다. " * 40 for i in range(120))


def test_ac_s20_map_reduce_call_count(client, orouter):
    n_chunks = len(summarizer._split_chunks(LONG.strip()))
    assert len(LONG) > 30_000 and n_chunks >= 2
    orouter.handler = lambda m, p, n: good_for(p)
    r = post(client, LONG)
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] == n_chunks
    assert len(orouter.calls) == n_chunks + 1  # Map N + Reduce 1
    prompts = [c["messages"][-1]["content"] for c in orouter.calls]
    assert sum("[문서 일부]" in p for p in prompts) == n_chunks
    assert "[부분별 노트]" in prompts[-1]


def test_map_concurrency_limited(client, orouter):
    peak = SimpleNamespace(v=0)

    async def slow(p):
        peak.v = max(peak.v, orouter.active)
        await asyncio.sleep(0.05)
        return good_for(p)
    orouter.handler = lambda m, p, n: slow(p)
    assert post(client, LONG).status_code == 200
    assert 1 <= peak.v <= summarizer.MAX_CONCURRENCY


def test_fallback_sticks_for_rest_of_request(client, orouter):
    orouter.handler = lambda m, p, n: status(429) if (m == PRIMARY and n == 1) else good_for(p)
    monkey_conc = summarizer.MAX_CONCURRENCY
    r = post(client, LONG)
    assert r.status_code == 200, r.text
    assert r.json()["fallback_used"] is True and r.json()["model"] == FALLBACK
    later = models(orouter)[monkey_conc + 1:]  # 이미 동시에 시작된 호출 이후로는 모두 폴백
    assert later and all(m == FALLBACK for m in later)


def test_map_failure_cancels_inflight_calls(client, orouter):
    """한 청크가 즉시 실패(401)하면 진행 중인 다른 청크 호출은 취소되어야 한다(누수 금지)."""
    async def slow(p):
        await asyncio.sleep(3)
        return good_for(p)
    orouter.handler = lambda m, p, n: status(401) if n == 1 else slow(p)
    t0 = time.monotonic()
    assert_error(post(client, LONG), "AI_AUTH_ERROR")
    assert time.monotonic() - t0 < 2.0
    time.sleep(0.2)
    assert orouter.active == 0


def test_ac_s22_total_budget_timeout(client, orouter, monkeypatch):
    monkeypatch.setattr(summarizer, "TOTAL_BUDGET", 1.5)
    monkeypatch.setattr(summarizer, "CALL_TIMEOUT", 1.0)

    async def slow(p):
        await asyncio.sleep(0.8)
        return good_for(p)
    orouter.handler = lambda m, p, n: slow(p)
    t0 = time.monotonic()
    assert_error(post(client, LONG), "AI_TIMEOUT")
    assert time.monotonic() - t0 < 1.5 + 1.0  # 예산을 크게 넘기지 않음
    time.sleep(0.2)
    assert orouter.active == 0  # 남은 호출 정리


def test_total_budget_hard_limit_even_if_call_hangs(client, orouter, monkeypatch):
    """개별 호출 타임아웃이 예산보다 길어도 전체 상한에서 끊는다."""
    monkeypatch.setattr(summarizer, "TOTAL_BUDGET", 1.0)
    monkeypatch.setattr(summarizer, "CALL_TIMEOUT", 10.0)

    async def hang():
        await asyncio.sleep(30)
    orouter.handler = lambda m, p, n: hang()
    t0 = time.monotonic()
    assert_error(post(client), "AI_TIMEOUT")
    assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------- 보안 (AC-S-21)

def test_ac_s21_key_and_body_not_logged(client, orouter, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "UNIQUE-DOC-MARKER-7f3a"
    orouter.handler = lambda m, p, n: status(429) if m == PRIMARY else chat(GOOD)
    r = post(client, TEXT + marker)
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    log = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_KEY not in log
    assert marker not in log
    assert PRIMARY in log  # 모델 ID는 로그에 남는다
