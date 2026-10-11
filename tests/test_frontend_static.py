"""index_pdf.html 정적 검사 — FEATURE_SPEC §5 (브라우저 E2E는 별도로 Playwright로 수행)."""
import re

import pytest

from backend import summarizer
from backend.errors import ERRORS
from tests.conftest import ROOT

HTML = (ROOT / "index_pdf.html").read_text(encoding="utf-8")

IDS = ["app-title", "server-banner", "drop-zone", "file-button", "file-input", "status", "doc-info",
       "error-box", "retry-button", "result", "one-line", "key-points", "detailed", "result-meta",
       "copy-button", "download-button", "reset-button", "notice"]


@pytest.mark.parametrize("id_", IDS)
def test_fixed_ids_present_once(id_):
    assert len(re.findall(rf'id="{id_}"', HTML)) == 1


def test_self_contained_no_external_resources():
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', HTML)
    assert "@import" not in HTML


def test_file_input_accept():
    assert 'accept=".pdf,application/pdf"' in HTML


def test_api_base_logic():
    assert re.search(r"location\.protocol\s*===\s*'file:'\s*\?\s*'http://localhost:8000'\s*:\s*''", HTML)


@pytest.mark.parametrize("code", ["INVALID_FILE_TYPE", "EMPTY_FILE", "FILE_TOO_LARGE", "TEXT_TOO_LONG"])
def test_client_side_messages_match_spec(code):
    assert ERRORS[code][1] in HTML


def test_fixed_ui_strings():
    for s in ["PDF 문서 요약", "파일 선택", "최대 20MB, PDF만 지원", "PDF 파일을 여기로 끌어다 놓거나",
              "업로드한 문서 내용은 요약을 위해 외부 AI 서비스(OpenRouter)로 전송됩니다.",
              "서버에 연결할 수 없습니다. 백엔드 서버(uvicorn backend.main:app --port 8000)가 실행 중인지 확인해 주세요.",
              "AI API 키가 설정되지 않아 요약을 사용할 수 없습니다.", "한 줄 요약", "핵심 포인트", "상세 요약",
              "복사되었습니다", "복사에 실패했습니다.", "다른 파일 요약하기", "다시 시도", "(대체 모델 사용)",
              "한 번에 하나의 파일만 요약할 수 있습니다. 첫 번째 파일만 처리합니다.", "_요약.md",
              "text/markdown;charset=utf-8", "PDF 업로드 및 텍스트 추출 중...",
              "AI가 요약하는 중입니다... (최대 1~3분 소요될 수 있습니다)"]:
        assert s in HTML, s


def test_no_html_injection_sinks():
    """§5.2: 동적 텍스트는 textContent로만 삽입 (XSS 방지)."""
    for sink in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("]:
        assert sink not in HTML, sink


def test_summarize_client_timeout_exceeds_server_budget():
    """클라이언트가 서버의 AI_TIMEOUT(전체 상한) 응답보다 먼저 끊으면 안 된다."""
    m = re.search(r"SUMMARIZE_TIMEOUT_MS\s*=\s*(\d+)", HTML)
    assert m, "요약 요청 전용 클라이언트 타임아웃 상수가 없음"
    assert int(m.group(1)) >= (summarizer.TOTAL_BUDGET + 10) * 1000


def test_upload_response_read_from_metadata():
    """§3.3 응답은 {text, metadata{...}} — 프론트가 최상위 page_count/char_count에 의존하면 안 된다."""
    assert "u.page_count" not in HTML and "u.char_count" not in HTML
    assert "metadata" in HTML
