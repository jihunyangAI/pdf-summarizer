import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.errors import AppError
from backend.pdf_extractor import extract_text
from backend.summarizer import get_models

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
logger = logging.getLogger("app")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 300_000
VERSION = "1.0.0"

# FEATURE_SPEC §2.3: file:// (Origin: null) 허용, 로컬 전용 앱
CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}

app = FastAPI(title="PDF 문서요약")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


def _err(code: str, headers: dict | None = None) -> JSONResponse:
    e = AppError(code)
    return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status,
                        headers=headers)


@app.exception_handler(AppError)
async def _app_error(_: Request, exc: AppError):
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/upload":
        for e in exc.errors():
            loc = tuple(e.get("loc") or ())
            if loc[-1:] == ("file",):
                return _err("NO_FILE")
    return _err("INVALID_REQUEST")


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _err("NOT_FOUND")
    if exc.status_code == 405:
        return _err("METHOD_NOT_ALLOWED")
    if exc.status_code < 500:  # 예: 깨진 multipart 본문
        return _err("INVALID_REQUEST")
    return _err("INTERNAL_ERROR")


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    # 미처리 예외 핸들러는 CORS 미들웨어 바깥(ServerErrorMiddleware)에서 실행되므로 CORS 헤더를 직접 붙인다.
    logger.error("unhandled %s", type(exc).__name__)
    return _err("INTERNAL_ERROR", headers=CORS_HEADERS)


@app.get("/")
async def index():
    page = ROOT / "index_pdf.html"
    if page.is_file():
        return FileResponse(page, media_type="text/html; charset=utf-8")
    return _err("NOT_FOUND")


@app.get("/api/health")
async def health():
    primary, fallback = get_models()
    return {
        "status": "ok",
        "api_key_configured": bool((os.getenv("OPENROUTER_API_KEY") or "").strip()),
        "default_model": primary,
        "fallback_model": fallback,
        "max_file_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "version": VERSION,
    }


def _basename(name: str) -> str:
    return re.split(r"[\\/]", name or "")[-1].strip()


@app.post("/api/upload")
async def upload(file: UploadFile):
    # FEATURE_SPEC §3.3 검증 순서: NO_FILE → EMPTY_FILE → FILE_TOO_LARGE → INVALID_FILE_TYPE → (추출)
    filename = _basename(file.filename or "")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(data) == 0:
        raise AppError("EMPTY_FILE")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("FILE_TOO_LARGE")
    if not filename.lower().endswith(".pdf") or b"%PDF-" not in data[:1024]:
        raise AppError("INVALID_FILE_TYPE")

    r = await run_in_threadpool(extract_text, data)  # CPU 작업이 이벤트 루프를 막지 않도록
    return {
        "text": r["text"],
        "metadata": {
            "filename": filename,
            "size_bytes": len(data),
            "page_count": r["page_count"],
            "char_count": r["char_count"],
            "title": r["title"],
            "author": r["author"],
        },
    }


@app.post("/api/summarize")
async def summarize_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise AppError("INVALID_REQUEST")
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        raise AppError("INVALID_REQUEST")
    # JSON may carry lone surrogate escapes (e.g. "\udcec") that cannot be UTF-8 encoded
    # for the OpenRouter request; replace them instead of failing with a 500.
    text = body["text"].encode("utf-8", "replace").decode("utf-8")
    if not text.strip():
        raise AppError("EMPTY_TEXT")
    if len(text) > MAX_TEXT_CHARS:
        raise AppError("TEXT_TOO_LONG")

    from backend.summarizer import summarize

    fn = body.get("filename")
    fn = fn.encode("utf-8", "replace").decode("utf-8") if isinstance(fn, str) else None
    return await summarize(text, fn)
