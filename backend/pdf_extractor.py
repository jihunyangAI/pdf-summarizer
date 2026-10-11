"""PDF 텍스트 추출 (메모리 처리, 디스크 저장 없음). FEATURE_SPEC §3.3."""
import re

import pymupdf

from backend.errors import AppError

MAX_PAGES = 300
MIN_TEXT_CHARS = 20


def _clean(text: str) -> str:
    """§3.3: 각 줄 오른쪽 공백 제거, 3개 이상 연속 빈 줄은 2개로 축소. 그 외 내용 변형 금지."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{4,}", "\n\n\n", text)


def extract_text(data: bytes) -> dict:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise AppError("CORRUPTED_PDF")

    try:
        if doc.needs_pass:
            raise AppError("ENCRYPTED_PDF")
        page_count = doc.page_count
        if page_count == 0:
            raise AppError("CORRUPTED_PDF")
        if page_count > MAX_PAGES:
            raise AppError("TOO_MANY_PAGES")
        try:
            pages = [doc[i].get_text("text") for i in range(page_count)]
        except Exception:
            raise AppError("CORRUPTED_PDF")
        meta = doc.metadata or {}
    finally:
        doc.close()

    text = _clean("\n\n".join(p.strip("\n") for p in pages)).strip()
    if len(re.sub(r"\s", "", text)) < MIN_TEXT_CHARS:
        raise AppError("NO_TEXT")

    return {
        "text": text,
        "page_count": page_count,
        "char_count": len(text),
        "title": (meta.get("title") or "").strip() or None,
        "author": (meta.get("author") or "").strip() or None,
    }
