"""OpenRouter 기반 한국어 요약기 (긴 텍스트는 map-reduce 청크 요약).

공개 인터페이스 (docs/FEATURE_SPEC.md 3.4):
    async def summarize(text: str, filename: str | None = None) -> dict
        -> {"summary": {"one_line", "key_points", "detailed"}, "model",
            "fallback_used", "elapsed_ms", "chunk_count"}
    get_models() -> (기본 모델, 폴백 모델)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from backend.errors import AppError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logger = logging.getLogger("summarizer")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# OpenRouter 모델 목록(2026-10-11)에 DeepSeek V3.1 무료(:free) 버전이 없다.
# 실측상 유료 deepseek-chat-v3.1 은 응답 지연 편차가 커(최대 60초) 기본은 V3.2,
# 폴백은 V3.1 로 한다. 둘 다 OPENROUTER_MODEL / OPENROUTER_FALLBACK_MODEL 로 덮어쓸 수 있다.
DEFAULT_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_FALLBACK_MODEL = "deepseek/deepseek-chat-v3.1"

CALL_TIMEOUT = 60.0             # 모델 호출 1회당 타임아웃(초)
TOTAL_BUDGET = 180.0            # 요약 전체 상한(초)
SINGLE_PASS_MAX_CHARS = 30_000  # 이 이하면 한 번에 요약
CHUNK_CHARS = 30_000            # 청크 크기 (300,000자 / 30,000 = 최대 10청크)
MAX_CONCURRENCY = 2
MAX_NOTES_CHARS = 30_000        # reduce 입력 상한. 넘으면 한 번 더 압축
MAX_TOKENS = 3000
MAP_MAX_TOKENS = 800            # 노트를 짧게 유지해 reduce 입력·지연을 줄인다

HEADERS_EXTRA = {
    "HTTP-Referer": "http://localhost:8000",
    "X-Title": "PDF Summarizer",
}

_SYSTEM = "당신은 문서를 정확하고 간결하게 요약하는 한국어 요약 전문가입니다. 문서에 없는 내용은 지어내지 않습니다."

_FINAL_PROMPT = """아래 문서를 한국어로 요약하세요. 문서가 영어 등 다른 언어여도 결과는 반드시 한국어로 작성합니다.
반드시 아래 JSON 객체 하나만 출력하세요. 코드블록이나 설명 문장은 쓰지 마세요.
key_points 항목에는 불릿 기호나 번호를 붙이지 마세요.
{{
  "one_line": "문서 전체를 한 문장으로 (80자 내외)",
  "key_points": ["핵심 포인트 3~7개, 각각 한 문장"],
  "detailed": "상세 요약. 주제별로 문단을 나누어 5~15문장, 문단 사이는 빈 줄"
}}
{name_line}
[문서]
{body}"""

_MAP_PROMPT = """아래는 긴 문서의 일부분({idx}/{total})입니다. 이후 전체 요약에 사용할 수 있도록
이 부분의 핵심 내용, 중요한 수치·고유명사·결론을 한국어 불릿 목록(5~10줄)으로 간결하게 정리하세요.
부가 설명 없이 불릿 목록만 출력하세요.

[문서 일부]
{body}"""

_REDUCE_PROMPT = """아래는 긴 문서를 순서대로 나눠 정리한 부분별 요약 노트입니다. 이를 종합해 문서 전체를 한국어로 요약하세요.
반드시 아래 JSON 객체 하나만 출력하세요. 코드블록이나 설명 문장은 쓰지 마세요.
key_points 항목에는 불릿 기호나 번호를 붙이지 마세요.
{{
  "one_line": "문서 전체를 한 문장으로 (80자 내외)",
  "key_points": ["핵심 포인트 3~7개, 각각 한 문장"],
  "detailed": "상세 요약. 주제별로 문단을 나누어 5~15문장, 문단 사이는 빈 줄"
}}
{name_line}
[부분별 노트]
{body}"""


class _CallFailure(Exception):
    """모델 호출 한 번의 실패.
    kind: timeout | deadline | rate_limit | server | network | model | auth | credit | parse | other"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# 폴백 모델로 재시도할 수 있는 실패 유형 (FEATURE_SPEC 3.4.2)
_FALLBACK_KINDS = {"timeout", "rate_limit", "server", "network", "model"}


# ---------------------------------------------------------------- 설정

def get_models() -> tuple[str, str]:
    """(기본 모델, 폴백 모델). 환경변수가 비어 있으면 상수 기본값."""
    primary = (os.getenv("OPENROUTER_MODEL") or "").strip() or DEFAULT_MODEL
    fallback = (os.getenv("OPENROUTER_FALLBACK_MODEL") or "").strip() or DEFAULT_FALLBACK_MODEL
    return primary, fallback


def _api_key() -> str:
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise AppError(
            "API_KEY_MISSING",
            "서버에 AI API 키가 설정되지 않았습니다. 관리자에게 문의하세요. (.env의 OPENROUTER_API_KEY 확인)",
            500,
        )
    return key


class _State:
    """요청 단위 상태. 한 번 폴백하면 이후 호출은 폴백 모델로 시작한다."""

    def __init__(self, key: str, client: httpx.AsyncClient, deadline: float):
        self.key, self.client, self.deadline = key, client, deadline
        self.primary, self.fallback = get_models()
        self.fallback_used = False

    @property
    def model(self) -> str:
        return self.fallback if self.fallback_used else self.primary

    def can_fallback(self) -> bool:
        return not self.fallback_used and bool(self.fallback) and self.fallback != self.primary


# ---------------------------------------------------------------- 파싱

_BULLET = re.compile(r"^\s*(?:[-•*·●]+|\d+[.)])\s*")


def _parse_summary(raw: str) -> dict:
    """모델 출력에서 요약 JSON을 견고하게 추출한다. 실패 시 _CallFailure('parse')."""
    if not raw or not raw.strip():
        raise _CallFailure("parse", "빈 응답")
    s = raw.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise _CallFailure("parse", "JSON 객체 없음")
    blob = s[start:end + 1]
    try:
        data = json.loads(blob, strict=False)
    except json.JSONDecodeError:
        try:  # 흔한 오류: 마지막 쉼표
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", blob), strict=False)
        except json.JSONDecodeError as e:
            raise _CallFailure("parse", f"JSON 해석 실패: {e}") from e
    if not isinstance(data, dict):
        raise _CallFailure("parse", "객체 아님")

    one_line = str(data.get("one_line") or "").strip()
    detailed = data.get("detailed") or ""
    if isinstance(detailed, list):
        detailed = "\n\n".join(str(x) for x in detailed)
    detailed = str(detailed).strip()
    kp = data.get("key_points") or []
    if isinstance(kp, str):
        kp = kp.splitlines()
    if not isinstance(kp, list):
        raise _CallFailure("parse", "key_points 형식 오류")
    key_points = []
    for x in kp:
        t = _BULLET.sub("", str(x)).strip()
        if t:
            key_points.append(t)
    key_points = key_points[:7]
    if not one_line or not detailed or len(key_points) < 3:
        raise _CallFailure("parse", "필수 필드 누락/부족")
    return {"one_line": one_line, "key_points": key_points, "detailed": detailed}


# ---------------------------------------------------------------- 호출

async def _call(st: _State, model: str, prompt: str, max_tokens: int) -> str:
    remaining = st.deadline - time.monotonic()
    if remaining <= 1:
        raise _CallFailure("deadline", "전체 시간 초과")
    timeout = min(CALL_TIMEOUT, remaining)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        # 같은 모델을 여러 제공자가 서비스하므로 처리량이 높은 제공자를 우선(지연 감소)
        "provider": {"sort": "throughput"},
    }
    headers = {"Authorization": f"Bearer {st.key}", "Content-Type": "application/json",
               **HEADERS_EXTRA}
    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            st.client.post(OPENROUTER_URL, json=payload, headers=headers), timeout=timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException) as e:
        logger.warning("model=%s timeout %.1fs", model, time.monotonic() - t0)
        if st.deadline - time.monotonic() <= 1:
            raise _CallFailure("deadline", model) from e
        raise _CallFailure("timeout", model) from e
    except httpx.HTTPError as e:
        logger.warning("model=%s network %s", model, type(e).__name__)
        raise _CallFailure("network", type(e).__name__) from e

    code = resp.status_code
    logger.info("model=%s status=%s %.1fs", model, code, time.monotonic() - t0)
    if code == 429:
        raise _CallFailure("rate_limit", model)
    if code in (401, 403):
        raise _CallFailure("auth", str(code))
    if code == 402:
        raise _CallFailure("credit", str(code))
    if code == 404:
        raise _CallFailure("model", str(code))
    if code == 400:
        kind = "model" if "model" in resp.text.lower() else "other"
        raise _CallFailure(kind, "400")
    if code >= 500:
        raise _CallFailure("server", str(code))
    if code >= 400:
        raise _CallFailure("other", str(code))
    try:
        body = resp.json()
    except ValueError as e:
        raise _CallFailure("server", "응답 JSON 아님") from e
    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        ecode = err.get("code") if isinstance(err, dict) else None
        if ecode == 429:
            raise _CallFailure("rate_limit", model)
        if ecode in (401, 403):
            raise _CallFailure("auth", str(ecode))
        if ecode == 402:
            raise _CallFailure("credit", "402")
        raise _CallFailure("server", f"error {ecode}")
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise _CallFailure("server", "choices 없음") from e
    return str(content or "")


async def _invoke(st: _State, prompt: str, parse=None, max_tokens: int = MAX_TOKENS):
    """호출 1건: 파싱 실패 시 같은 모델로 1회 재요청, 폴백 유형 실패 시 폴백으로 1회 전환."""
    parse_retried = False
    while True:
        model = st.model
        try:
            raw = await _call(st, model, prompt, max_tokens)
            if not raw.strip():
                raise _CallFailure("parse", "빈 응답")
            return parse(raw) if parse else raw.strip()
        except _CallFailure as f:
            if f.kind == "parse":
                if not parse_retried:
                    parse_retried = True
                    logger.warning("model=%s 파싱 실패, 재요청: %s", model, f.detail)
                    continue
                raise
            if f.kind in _FALLBACK_KINDS and st.can_fallback():
                logger.warning("model=%s 실패(%s) -> 폴백 모델 사용", model, f.kind)
                st.fallback_used = True
                continue
            raise


def _to_app_error(f: _CallFailure) -> AppError:
    k = f.kind
    if k in ("timeout", "deadline"):
        return AppError("AI_TIMEOUT", "AI 요약 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", 504)
    if k == "rate_limit":
        return AppError("AI_RATE_LIMIT", "AI 서비스 요청이 많아 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.", 429)
    if k == "auth":
        return AppError("AI_AUTH_ERROR", "AI 서비스 인증에 실패했습니다. API 키를 확인해 주세요.", 502)
    if k == "parse":
        return AppError("AI_INVALID_RESPONSE", "AI 응답을 해석하지 못했습니다. 다시 시도해 주세요.", 502)
    return AppError("AI_ERROR", "AI 요약 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", 502)


# ---------------------------------------------------------------- 청크

def _split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """size 이하로 분할. 경계: 마지막 빈 줄 -> 줄바꿈 -> 문장 끝 -> 강제 절단."""
    chunks: list[str] = []
    pos, n = 0, len(text)
    while pos < n:
        end = min(pos + size, n)
        if end < n:
            window = text[pos:end]
            cut = -1
            for seps in (("\n\n",), ("\n",), (". ", "다.", "? ", "! ")):
                best = max((window.rfind(sp) + len(sp) for sp in seps if window.rfind(sp) >= 0),
                           default=-1)
                if best >= size * 0.3:
                    cut = best
                    break
            if cut > 0:
                end = pos + cut
        piece = text[pos:end].strip()
        if piece:
            chunks.append(piece)
        pos = end
    return chunks


# ---------------------------------------------------------------- 공개 API

async def summarize(text: str, filename: str | None = None) -> dict:
    key = _api_key()
    text = (text or "").strip()
    if not text:
        raise AppError("EMPTY_TEXT", "요약할 텍스트가 비어 있습니다.", 400)
    name = (filename or "").strip()[:255]
    name_line = f"(파일명: {name})\n" if name else ""

    started = time.monotonic()
    timeout = httpx.Timeout(CALL_TIMEOUT + 5, connect=10.0)
    chunk_count = 1
    try:
        # 전체 상한(§3.4.2): 개별 호출 타임아웃과 무관하게 TOTAL_BUDGET에서 진행 중 호출을 모두 취소한다.
        async with asyncio.timeout(TOTAL_BUDGET):
            async with httpx.AsyncClient(timeout=timeout) as client:
                st = _State(key, client, started + TOTAL_BUDGET)
                if len(text) <= SINGLE_PASS_MAX_CHARS:
                    summary = await _invoke(
                        st, _FINAL_PROMPT.format(body=text, name_line=name_line), _parse_summary)
                else:
                    summary, chunk_count = await _map_reduce(st, text, name_line)
                model = st.model
    except TimeoutError as e:
        logger.warning("전체 처리 시간 %.0f초 초과", TOTAL_BUDGET)
        raise _to_app_error(_CallFailure("deadline")) from e
    except _CallFailure as f:
        raise _to_app_error(f) from f

    return {
        "summary": summary,
        "model": model,
        "fallback_used": st.fallback_used,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "chunk_count": chunk_count,
    }


async def _gather_all(coros) -> list:
    """asyncio.gather와 같지만, 하나가 실패하면 나머지 진행 중 호출을 취소하고 정리한 뒤 예외를 올린다."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _map_reduce(st: _State, text: str, name_line: str):
    chunks = _split_chunks(text)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def map_one(i: int, body: str, total: int) -> str:
        async with sem:
            return await _invoke(
                st, _MAP_PROMPT.format(idx=i + 1, total=total, body=body),
                None, MAP_MAX_TOKENS)

    notes = await _gather_all(map_one(i, c, len(chunks)) for i, c in enumerate(chunks))
    joined = "\n\n".join(f"[{i + 1}부]\n{n}" for i, n in enumerate(notes))

    if len(joined) > MAX_NOTES_CHARS:  # 노트가 너무 길면 한 번 더 압축
        groups = _split_chunks(joined, MAX_NOTES_CHARS)
        squeezed = await _gather_all(map_one(i, g, len(groups)) for i, g in enumerate(groups))
        joined = "\n\n".join(squeezed)[:MAX_NOTES_CHARS]

    summary = await _invoke(
        st, _REDUCE_PROMPT.format(body=joined, name_line=name_line), _parse_summary)
    return summary, len(chunks)
