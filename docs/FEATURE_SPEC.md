# PDF 문서요약 앱 — 기능명세 (FEATURE SPEC)

- 문서 버전: v1.0
- 작성일: 2026-10-11
- 상위 문서: [`docs/PRD.md`](./PRD.md)
- 이 문서는 API 계약·에러 코드·UI 동작·수용 기준(AC)의 **정본**이다. 백엔드·AI통합·프론트엔드·QA 에이전트는 이 문서만 보고 구현·테스트할 수 있어야 한다.

---

## 1. 시스템 구성

```
[브라우저: index_pdf.html]
   │  (file:// 로 열면 → http://localhost:8000 호출)
   │  (http://localhost:8000/ 로 열면 → 같은 출처 상대경로 /api/... 호출)
   ▼
[FastAPI: backend.main:app  (uvicorn backend.main:app --port 8000)]
   ├─ GET  /                → index_pdf.html 서빙
   ├─ GET  /api/health
   ├─ POST /api/upload      → PyMuPDF 텍스트 추출
   └─ POST /api/summarize   → OpenRouter (기본 모델 → 폴백 모델, 둘 다 저가 유료 DeepSeek)
```

### 1.1 파일 배치
| 경로 | 내용 |
|------|------|
| `index_pdf.html` (루트) | 단일 파일 프론트엔드 (HTML/CSS/JS 인라인, 외부 CDN 의존 없음 권장) |
| `backend/main.py` | FastAPI 앱 객체 `app`, 라우트, 예외 핸들러, CORS |
| `backend/` 기타 모듈 | PDF 추출, OpenRouter 클라이언트, 설정 등 (구성은 백엔드 에이전트 재량) |
| `requirements.txt` (루트) | `fastapi`, `uvicorn`, `pymupdf`, `python-dotenv`, `python-multipart`, HTTP 클라이언트(`httpx` 권장) — Python 3.14 호환 버전 |
| `.env` (루트) | `OPENROUTER_API_KEY=...` (커밋·출력 금지) |
| `tests/` | pytest 테스트 및 테스트용 PDF 픽스처 |

### 1.2 설정값 (백엔드 상수 / 환경변수)
| 이름 | 기본값 | 설명 |
|------|--------|------|
| `OPENROUTER_API_KEY` | (없음) | `.env`에서 `python-dotenv`로 로드. 프로젝트 루트 `.env` 경로를 명시적으로 지정해 로드(실행 위치 무관) |
| `OPENROUTER_MODEL` | `deepseek/deepseek-v3.2` | 기본 모델 (저가 유료). 운영 `.env`는 `deepseek/deepseek-chat`으로 덮어씀 |
| `OPENROUTER_FALLBACK_MODEL` | `deepseek/deepseek-chat-v3.1` | 폴백 모델 (저가 유료) |
| `MAX_FILE_BYTES` | 20,971,520 (20MB) | 업로드 상한 |
| `MAX_PAGES` | 300 | 페이지 상한 |
| `MAX_TEXT_CHARS` | 300,000 | 요약 입력 상한 |
| `CHUNK_CHARS` | 30,000 | 청크 크기 |
| `MIN_TEXT_CHARS` | 20 | 공백 제거 후 이 값 미만이면 `NO_TEXT` |
| `MODEL_TIMEOUT_SEC` | 60 | 모델 호출 1회 타임아웃 |
| `TOTAL_TIMEOUT_SEC` | 180 | `/api/summarize` 전체 상한 (프론트엔드 요약 요청 타임아웃은 이보다 긴 200초 — 서버의 `AI_TIMEOUT` 응답을 먼저 받기 위함) |

> 모델 관련 확정 사항(2026-10-11): 당초 기본 모델로 정한 무료 `deepseek/deepseek-chat-v3.1:free`는 **OpenRouter에 존재하지 않는다**. 사용자 지시("없거나 너무 느리면 다른 적당한 유료모델")에 따라 기본·폴백 모두 저가 유료 DeepSeek 모델을 사용한다. 실제 사용 중인 모델은 `/api/health`의 `default_model`/`fallback_model`로 확인한다.

---

## 2. 공통 규약

### 2.1 공통 응답 규칙
- 모든 `/api/*` 응답은 `Content-Type: application/json; charset=utf-8`.
- 한글은 이스케이프 없이 UTF-8로 그대로 전송해도 되고 `\uXXXX` 이스케이프여도 된다(클라이언트는 JSON 파싱 후 사용).
- 성공: HTTP 200 + 엔드포인트별 본문.
- 실패: 아래 **통일 에러 형식** + 에러 코드 표의 HTTP 상태.

### 2.2 통일 에러 형식
```json
{
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "파일 크기가 20MB를 초과합니다. 20MB 이하의 PDF를 업로드해 주세요."
  }
}
```
- `error.code`: 대문자 스네이크 케이스, §4 표에 정의된 값만 사용.
- `error.message`: 사용자에게 그대로 보여줄 수 있는 한국어 문장. 내부 예외 메시지, 스택트레이스, 파일 시스템 경로, API 키를 포함하지 않는다.
- 추가 필드 금지(`error` 외 최상위 키 없음). 단, 디버깅용 `error.detail`(문자열, 선택)은 키·경로를 포함하지 않는 경우에 한해 허용.
- FastAPI 기본 에러(`{"detail": ...}`)와 요청 검증 에러(기본 422)는 반드시 이 형식으로 변환한다 (`RequestValidationError` → `INVALID_REQUEST` 400, 404 → `NOT_FOUND`, 405 → `METHOD_NOT_ALLOWED`, 미처리 예외 → `INTERNAL_ERROR` 500).

### 2.3 CORS
- `file://`에서 연 페이지는 `Origin: null`을 보내므로 이를 허용해야 한다. 로컬 전용 앱이므로 `allow_origins=["*"]`, `allow_methods=["GET","POST","OPTIONS"]`, `allow_headers=["*"]`, `allow_credentials=False`.
- 에러 응답(4xx/5xx)에도 CORS 헤더가 포함되어야 브라우저에서 에러 메시지를 읽을 수 있다.

---

## 3. API 명세

### 3.1 `GET /`
- 응답: 200, `text/html; charset=utf-8`, 루트의 `index_pdf.html` 내용.
- 파일이 없으면 404 `NOT_FOUND`.

### 3.2 `GET /api/health`
외부(OpenRouter) 호출 없이 즉시 응답한다. API 키가 없어도 200.

응답 200:
```json
{
  "status": "ok",
  "api_key_configured": true,
  "default_model": "deepseek/deepseek-v3.2",
  "fallback_model": "deepseek/deepseek-chat-v3.1",
  "max_file_mb": 20,
  "version": "1.0.0"
}
```
| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | 항상 `"ok"` |
| `api_key_configured` | boolean | `OPENROUTER_API_KEY`가 비어 있지 않으면 `true` (키 값 자체는 절대 포함 금지) |
| `default_model` | string | 기본 모델 ID |
| `fallback_model` | string | 폴백 모델 ID |
| `max_file_mb` | integer | 20 |
| `version` | string | 앱 버전 |

### 3.3 `POST /api/upload`
요청: `multipart/form-data`, 필드명 **`file`** (단일 파일).

검증 순서 (먼저 걸리는 에러 하나만 반환):
1. `file` 필드 없음 → `NO_FILE`
2. 크기 0바이트 → `EMPTY_FILE`
3. 크기 > 20,971,520바이트 → `FILE_TOO_LARGE` (정확히 20,971,520바이트는 허용)
4. 파일명 확장자가 `.pdf`(대소문자 무관)가 아니거나, 내용 앞 1024바이트 안에 `%PDF-` 시그니처가 없음 → `INVALID_FILE_TYPE`
   - `Content-Type` 헤더는 참고만 하고 판정 근거로 쓰지 않는다(브라우저마다 다름).
5. PyMuPDF로 열기 실패 → `CORRUPTED_PDF`
6. 열기 암호 필요(`needs_pass`/인증 실패) → `ENCRYPTED_PDF`
7. 페이지 수 0 → `CORRUPTED_PDF`; 페이지 수 > 300 → `TOO_MANY_PAGES`
8. 페이지별 텍스트 추출 → 공백(스페이스·탭·줄바꿈) 제거 후 글자 수 < 20 → `NO_TEXT`

텍스트 추출 규칙:
- 페이지 순서대로 `page.get_text()` 결과를 이어 붙이며 페이지 사이에 `\n\n` 삽입.
- 각 줄의 오른쪽 공백 제거, 3개 이상 연속 빈 줄은 2개로 축소. 그 외 내용 변형 금지(한글 정규화 등 하지 않음).
- 파일은 디스크에 저장하지 않는다.

응답 200:
```json
{
  "text": "1장 서론\n본 보고서는 ...\n\n2장 ...",
  "metadata": {
    "filename": "보고서.pdf",
    "size_bytes": 1048576,
    "page_count": 12,
    "char_count": 23456,
    "title": "2026 시장 보고서",
    "author": "홍길동"
  }
}
```
| 필드 | 타입 | 설명 |
|------|------|------|
| `text` | string | 추출된 전체 텍스트 |
| `metadata.filename` | string | 업로드된 원본 파일명 (경로 제거, 기본 이름만) |
| `metadata.size_bytes` | integer | 파일 바이트 수 |
| `metadata.page_count` | integer | 페이지 수 (≥ 1) |
| `metadata.char_count` | integer | `len(text)` (공백 포함) |
| `metadata.title` | string \| null | PDF 메타데이터 제목, 없거나 빈 문자열이면 `null` |
| `metadata.author` | string \| null | PDF 메타데이터 작성자, 없거나 빈 문자열이면 `null` |

> 참고: `char_count`가 300,000을 넘어도 업로드 자체는 성공한다. 이 경우 프론트엔드는 요약 호출 전에 "문서가 너무 길어 요약할 수 없습니다" 안내를 표시하고 요약을 호출하지 않는다(서버도 `TEXT_TOO_LONG`으로 거부).

### 3.4 `POST /api/summarize`
요청: `Content-Type: application/json`
```json
{
  "text": "추출된 전체 텍스트 ...",
  "filename": "보고서.pdf"
}
```
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `text` | string | O | 요약할 텍스트. 공백 제거 후 1자 이상, 길이(`len`) ≤ 300,000 |
| `filename` | string | X | 프롬프트 맥락용(선택). 최대 255자, 초과 시 잘라서 사용 |

검증:
- 본문이 JSON이 아니거나 `text`가 없음/문자열 아님 → `INVALID_REQUEST` (400)
- `text.strip()`이 빈 문자열 → `EMPTY_TEXT` (400)
- `len(text)` > 300,000 → `TEXT_TOO_LONG` (413)
- `OPENROUTER_API_KEY` 미설정/빈 값 → `API_KEY_MISSING` (500) — 외부 호출 전에 판정

응답 200:
```json
{
  "summary": {
    "one_line": "이 보고서는 2026년 국내 전기차 시장이 전년 대비 25% 성장할 것으로 전망한다.",
    "key_points": [
      "국내 전기차 판매량은 2026년 약 40만 대로 예상된다.",
      "충전 인프라 확충이 성장의 핵심 변수로 꼽힌다.",
      "보조금 축소가 중저가 모델 경쟁을 촉진한다."
    ],
    "detailed": "첫 번째 문단 ...\n\n두 번째 문단 ...\n\n세 번째 문단 ..."
  },
  "model": "deepseek/deepseek-v3.2",
  "fallback_used": false,
  "elapsed_ms": 18342,
  "chunk_count": 1
}
```
| 필드 | 타입 | 설명 |
|------|------|------|
| `summary.one_line` | string | 1문장 한국어 요약 (비어 있지 않음) |
| `summary.key_points` | string[] | 3~7개, 각 항목 비어 있지 않은 문자열, 앞에 `-`, `•`, `1.` 같은 불릿/번호 없음 |
| `summary.detailed` | string | 비어 있지 않은 한국어 상세 요약, 문단 구분 `\n\n` |
| `model` | string | 최종 요약(Reduce 단계 또는 단일 호출)에 실제 사용된 모델 ID |
| `fallback_used` | boolean | 요청 처리 중 한 번이라도 폴백 모델을 썼으면 `true` |
| `elapsed_ms` | integer | 서버가 요청 수신~응답 생성까지 걸린 시간(밀리초) |
| `chunk_count` | integer | 1 = 단일 호출, ≥ 2 = 청크 분할(Map-Reduce) |

#### 3.4.1 AI 호출 규칙 (AI통합 에이전트 구현)
- 엔드포인트: `https://openrouter.ai/api/v1/chat/completions`, 헤더 `Authorization: Bearer <키>`, 권장 헤더 `HTTP-Referer`, `X-Title`(예: `PDF 요약 앱`).
- 프롬프트: 한국어로 요약하라는 지시 + 출력은 아래 JSON만 반환하도록 지시.
  ```json
  {"one_line": "...", "key_points": ["...", "..."], "detailed": "..."}
  ```
  가능하면 `response_format: {"type":"json_object"}` 사용(모델 미지원 시 생략). 응답의 코드펜스(```json ... ```)는 서버가 제거 후 파싱.
- 응답 정규화: `key_points`가 7개 초과면 앞 7개만, 각 항목 앞 불릿/번호 기호 제거, 양끝 공백 제거. 3개 미만이거나 필수 필드 누락/빈 값이면 **파싱 실패**로 간주.
- 파싱 실패 시 같은 모델로 1회 재요청 → 또 실패하면 `AI_INVALID_RESPONSE`.

#### 3.4.2 폴백 규칙
| 기본 모델 결과 | 동작 |
|----------------|------|
| 성공 | 그대로 반환 (`fallback_used=false`) |
| HTTP 429 | 폴백 모델로 재시도 |
| HTTP 5xx (500~599) | 폴백 모델로 재시도 |
| 60초 타임아웃 | 폴백 모델로 재시도 |
| 네트워크 오류(연결 실패 등) | 폴백 모델로 재시도 |
| 모델 없음/사용 불가 (HTTP 404, 또는 400이면서 모델 관련 오류) | 폴백 모델로 재시도 |
| HTTP 401/403 (인증 실패) | 폴백 없이 즉시 `AI_AUTH_ERROR` |
| HTTP 402 (크레딧 부족) | 폴백 없이 즉시 `AI_ERROR` |

- 폴백 재시도는 **호출당 1회**. 폴백도 실패하면 폴백 모델의 실패 유형으로 최종 에러 결정: 429 → `AI_RATE_LIMIT`, 타임아웃 → `AI_TIMEOUT`, 401/403 → `AI_AUTH_ERROR`, 그 외 → `AI_ERROR`.
- 요청 내에서 한 번 폴백이 발생하면 이후 청크/Reduce 호출은 처음부터 폴백 모델 사용.
- 전체 처리 시간이 180초를 넘으면 진행 중 호출을 중단하고 `AI_TIMEOUT`.
- 서버 로그에는 모델 ID, HTTP 상태, 소요 시간만 남기고 키·본문 전체는 남기지 않는다.

#### 3.4.3 청크 분할 (텍스트 > 30,000자)
- 30,000자 이하 단위로 분할; 분할 지점은 해당 범위 안에서 마지막 `\n\n` → `\n` → 문장 끝(`. `, `다.`, `? `, `! `) → 강제 절단 순으로 선택.
- Map: 청크별 부분 요약(자유 텍스트, 한국어). Reduce: 부분 요약을 순서대로 합쳐 최종 JSON 요약 1회.
- Map 호출은 순차 또는 동시성 2 이하(429 방지, 가정). Map 노트는 5~10줄로 짧게 유지해 Reduce 입력과 지연을 줄인다.
- 한 Map 호출이 폴백 불가 오류로 실패하면 진행 중인 나머지 호출은 즉시 취소한다.
- `chunk_count` = 청크 개수.

---

## 4. 에러 코드 표

| code | HTTP | 발생 엔드포인트 | 사용자 메시지 (`error.message`) |
|------|------|-----------------|-------------------------------|
| `NO_FILE` | 400 | upload | 업로드할 파일이 없습니다. PDF 파일을 선택해 주세요. |
| `INVALID_FILE_TYPE` | 400 | upload | PDF 파일만 업로드할 수 있습니다. |
| `EMPTY_FILE` | 400 | upload | 빈 파일입니다. 내용이 있는 PDF를 업로드해 주세요. |
| `FILE_TOO_LARGE` | 413 | upload | 파일 크기가 20MB를 초과합니다. 20MB 이하의 PDF를 업로드해 주세요. |
| `ENCRYPTED_PDF` | 422 | upload | 암호가 설정된 PDF는 지원하지 않습니다. 암호를 해제한 뒤 다시 업로드해 주세요. |
| `CORRUPTED_PDF` | 422 | upload | PDF 파일이 손상되어 열 수 없습니다. 다른 파일로 시도해 주세요. |
| `TOO_MANY_PAGES` | 413 | upload | 페이지 수가 너무 많습니다. 300페이지 이하의 PDF를 업로드해 주세요. |
| `NO_TEXT` | 422 | upload | PDF에서 텍스트를 추출할 수 없습니다. 스캔된 이미지 PDF는 지원하지 않습니다. |
| `INVALID_REQUEST` | 400 | summarize, 공통 | 요청 형식이 올바르지 않습니다. |
| `EMPTY_TEXT` | 400 | summarize | 요약할 텍스트가 비어 있습니다. |
| `TEXT_TOO_LONG` | 413 | summarize | 문서가 너무 길어 요약할 수 없습니다. (최대 300,000자) |
| `API_KEY_MISSING` | 500 | summarize | 서버에 AI API 키가 설정되지 않았습니다. 관리자에게 문의하세요. (.env의 OPENROUTER_API_KEY 확인) |
| `AI_AUTH_ERROR` | 502 | summarize | AI 서비스 인증에 실패했습니다. API 키를 확인해 주세요. |
| `AI_TIMEOUT` | 504 | summarize | AI 요약 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요. |
| `AI_RATE_LIMIT` | 429 | summarize | AI 서비스 요청이 많아 처리할 수 없습니다. 잠시 후 다시 시도해 주세요. |
| `AI_INVALID_RESPONSE` | 502 | summarize | AI 응답을 해석하지 못했습니다. 다시 시도해 주세요. |
| `AI_ERROR` | 502 | summarize | AI 요약 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. |
| `NOT_FOUND` | 404 | 공통 | 요청한 경로를 찾을 수 없습니다. |
| `METHOD_NOT_ALLOWED` | 405 | 공통 | 허용되지 않은 요청 방식입니다. |
| `INTERNAL_ERROR` | 500 | 공통 | 서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. |

프론트엔드 전용(서버가 반환하지 않음):
| code | 상황 | 표시 메시지 |
|------|------|-------------|
| `NETWORK_ERROR` | `fetch` 자체 실패(서버 미실행, CORS 차단 등) | 서버에 연결할 수 없습니다. 백엔드 서버(uvicorn backend.main:app --port 8000)가 실행 중인지 확인해 주세요. |
| `UNKNOWN_ERROR` | 응답이 에러 형식이 아니거나 JSON 파싱 실패 | 알 수 없는 오류가 발생했습니다. (HTTP 상태코드 표시) |

> 메시지 문구는 위 표와 **정확히 일치**해야 한다(QA가 문자열 비교로 검증). 단, `API_KEY_MISSING` 메시지의 괄호 부분은 포함한다.

---

## 5. 프론트엔드 명세 (`index_pdf.html`)

### 5.1 API 베이스 URL
- `location.protocol === "file:"`이면 `API_BASE = "http://localhost:8000"`.
- 그 외(서버에서 서빙)면 `API_BASE = ""`(같은 출처 상대경로).

### 5.2 화면 구성 (모두 한국어)
| 영역 | `id` (QA 셀렉터, 고정) | 내용 |
|------|------------------------|------|
| 제목 | `app-title` | "PDF 문서 요약" |
| 서버 상태 배너 | `server-banner` | `/api/health` 실패 시 `NETWORK_ERROR` 메시지 표시, API 키 미설정 시 "AI API 키가 설정되지 않아 요약을 사용할 수 없습니다." 표시. 정상 시 숨김 |
| 업로드 영역 | `drop-zone` | "PDF 파일을 여기로 끌어다 놓거나" + 버튼, "최대 20MB, PDF만 지원" 안내 |
| 파일 선택 버튼 | `file-button` | "파일 선택" |
| 파일 입력 | `file-input` | `type=file`, `accept=".pdf,application/pdf"`, 숨김 |
| 진행 상태 | `status` | 단계 문구 + 스피너 |
| 문서 정보 | `doc-info` | 파일명, 페이지 수, 글자 수(천 단위 구분), 제목/작성자(있을 때) |
| 에러 표시 | `error-box` | 에러 메시지 + "다시 시도" 버튼(`retry-button`) |
| 결과 영역 | `result` | 아래 3개 섹션 + 메타 |
| 한 줄 요약 | `one-line` | 섹션 제목 "한 줄 요약" |
| 핵심 포인트 | `key-points` | 섹션 제목 "핵심 포인트", `<ul>`의 `<li>` 목록 |
| 상세 요약 | `detailed` | 섹션 제목 "상세 요약", `\n\n` 기준 `<p>` 문단 |
| 결과 메타 | `result-meta` | "사용 모델: {model}" (+ 폴백 시 "(대체 모델 사용)"), "소요 시간: {elapsed_ms/1000 소수 1자리}초" |
| 복사 버튼 | `copy-button` | "복사" → 성공 시 2초간 "복사되었습니다" |
| 다운로드 버튼 | `download-button` | "다운로드" → `{원본파일명(확장자 제외)}_요약.md` |
| 새 파일 버튼 | `reset-button` | "다른 파일 요약하기" |
| 외부 전송 고지 | `notice` | "업로드한 문서 내용은 요약을 위해 외부 AI 서비스(OpenRouter)로 전송됩니다." |

- 모든 동적 텍스트는 `textContent`로 삽입(HTML 주입 금지, XSS 방지).

### 5.3 상태 흐름
```
idle → (파일 선택/드롭) → validating → uploading("PDF 업로드 및 텍스트 추출 중...")
     → summarizing("AI가 요약하는 중입니다... (최대 1~3분 소요될 수 있습니다)")
     → done
어느 단계든 실패 → error
error --(다시 시도)--> 실패한 단계부터 재시도 (업로드 실패 시 파일 재선택 필요, 요약 실패 시 저장된 text로 summarize만 재호출)
done/error --(다른 파일 요약하기)--> idle
```
- `uploading`/`summarizing` 중에는 드롭/파일 선택 비활성화(추가 드롭 무시).
- 드래그 진입 시 `drop-zone`에 강조 클래스(`dragover`) 추가, 이탈/드롭 시 제거.
- 페이지 전체에 파일을 떨어뜨려도 브라우저가 PDF를 새 탭으로 열지 않도록 기본 동작 방지.
- 여러 파일을 드롭하면 첫 번째 파일만 처리하고 "한 번에 하나의 파일만 요약할 수 있습니다. 첫 번째 파일만 처리합니다." 안내.

### 5.4 클라이언트 사전 검증 (서버 호출 전)
| 조건 | 표시 메시지 | 서버 호출 |
|------|-------------|-----------|
| 확장자 `.pdf` 아님 | `INVALID_FILE_TYPE` 메시지 | 안 함 |
| 0바이트 | `EMPTY_FILE` 메시지 | 안 함 |
| > 20MB | `FILE_TOO_LARGE` 메시지 | 안 함 |
| 업로드 성공 후 `char_count` > 300,000 | `TEXT_TOO_LONG` 메시지 (문서 정보는 표시) | summarize 안 함 |

### 5.5 복사/다운로드 텍스트 형식
```
# {파일명} 요약

## 한 줄 요약
{one_line}

## 핵심 포인트
- {key_points[0]}
- {key_points[1]}
...

## 상세 요약
{detailed}

---
모델: {model} / 소요 시간: {초}초
```
- 다운로드 파일 인코딩 UTF-8, MIME `text/markdown;charset=utf-8`.
- 클립보드 API 실패 시(예: 권한 문제) 숨김 `textarea` + `document.execCommand('copy')` 대체, 그래도 실패하면 "복사에 실패했습니다." 표시.

---

## 6. 수용 기준 (Acceptance Criteria)

표기: **Given** 전제 / **When** 행동 / **Then** 기대 결과. 우선순위 P0/P1. 테스트 픽스처는 `tests/fixtures/`에 둔다(QA가 생성).

### 6.0 테스트 픽스처 정의
| 픽스처 | 내용 |
|--------|------|
| `text_1p_ko.pdf` | 1페이지, 한글 문단 약 1,000자 |
| `text_multi_mixed.pdf` | 5페이지, 페이지마다 "PAGE-n" 표식 + 한글/영문 혼합 |
| `long_text.pdf` | 추출 텍스트 30,000자 초과~300,000자 이하 |
| `huge_text.pdf` | 추출 텍스트 300,000자 초과, 300페이지 이하, 20MB 이하 |
| `scanned.pdf` | 이미지만 있는 PDF(텍스트 레이어 없음) |
| `encrypted.pdf` | 열기 암호 설정 PDF |
| `corrupted.pdf` | `%PDF-` 헤더는 있으나 나머지가 깨진 파일 |
| `fake.pdf` | 확장자만 .pdf인 텍스트 파일 |
| `empty.pdf` | 0바이트 |
| `big_21mb.pdf` | 20MB 초과 파일 |
| `exact_20mb.pdf` | 정확히 20,971,520바이트의 유효 PDF (선택) |
| `pages_301.pdf` | 301페이지 텍스트 PDF |
| `sample.txt` | 일반 텍스트 파일 |
| `UPPER.PDF` | 대문자 확장자 유효 PDF |

### 6.1 헬스체크 (`GET /api/health`)
| ID | P | Given / When / Then |
|----|---|---------------------|
| AC-H-01 | P0 | 서버 실행 중 / `GET /api/health` / 200, `status=="ok"`, `api_key_configured`는 boolean, `default_model`·`fallback_model`은 비어 있지 않은 문자열, `max_file_mb==20` |
| AC-H-02 | P0 | `.env`에 키 있음 / 호출 / `api_key_configured==true`, 응답 본문 어디에도 키 문자열이 없음 |
| AC-H-03 | P0 | 키 미설정(환경에서 제거) / 호출 / 200, `api_key_configured==false` |
| AC-H-04 | P0 | 외부 네트워크 차단 / 호출 / 200을 200ms 이내 반환(외부 호출 없음) |

### 6.2 업로드 (`POST /api/upload`)
| ID | P | Given / When / Then |
|----|---|---------------------|
| AC-U-01 | P0 | `text_1p_ko.pdf` / 필드 `file`로 업로드 / 200, `text`에 원문 한글 문장 포함(깨짐 없음), `metadata.page_count==1`, `char_count==len(text)`, `filename=="text_1p_ko.pdf"`, `size_bytes`==파일 크기 |
| AC-U-02 | P0 | `text_multi_mixed.pdf` / 업로드 / 200, `page_count==5`, `text`에 "PAGE-1"~"PAGE-5"가 순서대로 등장, 영문·한글 모두 포함 |
| AC-U-03 | P0 | `scanned.pdf` / 업로드 / 422, `error.code=="NO_TEXT"`, 메시지가 §4와 일치 |
| AC-U-04 | P0 | `encrypted.pdf` / 업로드 / 422, `ENCRYPTED_PDF` |
| AC-U-05 | P0 | `corrupted.pdf` / 업로드 / 422, `CORRUPTED_PDF` |
| AC-U-06 | P0 | `fake.pdf` / 업로드 / 400, `INVALID_FILE_TYPE` |
| AC-U-07 | P0 | `sample.txt` / 업로드 / 400, `INVALID_FILE_TYPE` |
| AC-U-08 | P0 | `empty.pdf` / 업로드 / 400, `EMPTY_FILE` |
| AC-U-09 | P0 | `big_21mb.pdf` / 업로드 / 413, `FILE_TOO_LARGE` |
| AC-U-10 | P1 | `exact_20mb.pdf` / 업로드 / 413이 아님(경계값 허용) |
| AC-U-11 | P0 | 필드명을 `pdf`로 잘못 보냄 또는 파일 없이 POST / 400, `NO_FILE` |
| AC-U-12 | P1 | `pages_301.pdf` / 업로드 / 413, `TOO_MANY_PAGES` |
| AC-U-13 | P1 | `UPPER.PDF` / 업로드 / 200 |
| AC-U-14 | P0 | 임의 업로드 후 / 서버 작업 디렉터리 확인 / 업로드 파일이 디스크에 남지 않음 |
| AC-U-15 | P1 | PDF에 제목/작성자 메타데이터 없음 / 업로드 / `title==null`, `author==null` |
| AC-U-16 | P0 | 모든 에러 케이스 / 응답 확인 / 본문 최상위 키가 `error` 하나, `error.code`·`error.message` 모두 비어 있지 않은 문자열, `detail` 최상위 키 없음 |

### 6.3 요약 (`POST /api/summarize`)
외부 AI 의존 케이스는 OpenRouter 호출을 모킹(mock)한 단위 테스트 + 실제 키로 하는 수동/통합 테스트 1회로 검증.

| ID | P | Given / When / Then |
|----|---|---------------------|
| AC-S-01 | P0 | 유효 키, `text_1p_ko.pdf`의 `text` / `{"text": ..., "filename": "text_1p_ko.pdf"}` POST / 200, `summary.one_line` 비어 있지 않음, `key_points` 길이 3~7, 각 항목 비어 있지 않고 불릿 기호(`-`,`•`,`*`,`숫자.`)로 시작하지 않음, `detailed` 비어 있지 않음, `model` 비어 있지 않음, `fallback_used` boolean, `elapsed_ms` ≥ 0 정수, `chunk_count==1` |
| AC-S-02 | P0 | 영문 위주 텍스트 / 요약 / `one_line`·`detailed`에 한글이 포함(한국어 출력) |
| AC-S-03 | P0 | `{"text": "   \n "}` / POST / 400, `EMPTY_TEXT` |
| AC-S-04 | P0 | `{}` 또는 `{"text": 123}` 또는 JSON 아닌 본문 / POST / 400, `INVALID_REQUEST` (FastAPI 기본 422 아님) |
| AC-S-05 | P0 | 300,001자 텍스트 / POST / 413, `TEXT_TOO_LONG` (외부 호출 없음) |
| AC-S-06 | P0 | 키 미설정 / 유효 텍스트 POST / 500, `API_KEY_MISSING`, 외부 호출 없음 |
| AC-S-07 | P0 | (모킹) 기본 모델 429 → 폴백 성공 / POST / 200, `fallback_used==true`, `model==폴백 모델 ID` |
| AC-S-08 | P0 | (모킹) 기본 모델 503 → 폴백 성공 / POST / 200, `fallback_used==true` |
| AC-S-09 | P0 | (모킹) 기본 모델 60초 초과 → 폴백 성공 / POST / 200, `fallback_used==true`; 기본 모델 호출이 60초(+허용오차 2초) 시점에 중단됨 |
| AC-S-10 | P0 | (모킹) 기본·폴백 모두 429 / POST / 429, `AI_RATE_LIMIT` |
| AC-S-11 | P0 | (모킹) 기본·폴백 모두 타임아웃 / POST / 504, `AI_TIMEOUT` |
| AC-S-12 | P0 | (모킹) 기본·폴백 모두 500 / POST / 502, `AI_ERROR` |
| AC-S-13 | P0 | (모킹) 기본 모델 401 / POST / 502, `AI_AUTH_ERROR`, 폴백 호출 0회 |
| AC-S-14 | P1 | (모킹) 기본 모델 404(모델 없음) → 폴백 성공 / POST / 200, `fallback_used==true` |
| AC-S-15 | P0 | (모킹) 모델이 2회 연속 JSON 아닌 응답 / POST / 502, `AI_INVALID_RESPONSE` |
| AC-S-16 | P1 | (모킹) 모델 응답이 ```json 코드펜스로 감싸짐 / POST / 200, 정상 파싱 |
| AC-S-17 | P1 | (모킹) `key_points` 10개 반환 / POST / 응답 `key_points` 길이 7 |
| AC-S-18 | P1 | (모킹) `key_points` 항목이 "- 내용", "1. 내용" / POST / 응답 항목이 "내용"으로 정규화 |
| AC-S-19 | P1 | `long_text.pdf` 텍스트(30,000자 초과) / POST / 200, `chunk_count ≥ 2`, 3단 요약 정상 |
| AC-S-20 | P1 | (모킹) 청크 N개 / POST / 모델 호출 횟수 == N(Map) + 1(Reduce) |
| AC-S-21 | P0 | 모든 요약 요청 / 서버 로그·응답 확인 / API 키 문자열이 나타나지 않음 |
| AC-S-22 | P1 | (모킹) 전체 처리 180초 초과 / POST / 504, `AI_TIMEOUT` |

### 6.4 공통/서빙/CORS
| ID | P | Given / When / Then |
|----|---|---------------------|
| AC-C-01 | P0 | 서버 실행 / `GET /` / 200, `text/html`, 본문이 `index_pdf.html`과 동일 |
| AC-C-02 | P0 | `Origin: null` 헤더로 `OPTIONS /api/upload` 프리플라이트 / 응답에 `Access-Control-Allow-Origin` 포함(`*` 또는 `null`) |
| AC-C-03 | P0 | `Origin: null`로 에러 유발 요청(예: `fake.pdf`) / 400 응답에도 CORS 헤더 포함 |
| AC-C-04 | P1 | `GET /api/nope` / 404, `{"error":{"code":"NOT_FOUND",...}}` |
| AC-C-05 | P1 | `GET /api/upload` / 405, `METHOD_NOT_ALLOWED` |
| AC-C-06 | P0 | 프로젝트 루트에서 `uvicorn backend.main:app --port 8000` / 실행 / 오류 없이 기동, Python 3.14에서 의존성 설치 성공 |

### 6.5 프론트엔드 (브라우저 E2E)
| ID | P | Given / When / Then |
|----|---|---------------------|
| AC-F-01 | P0 | `http://localhost:8000/` 접속 / `text_1p_ko.pdf`를 `drop-zone`에 드롭 / `status`에 업로드→요약 단계 문구가 순서대로 표시, 완료 후 `doc-info`(파일명·페이지 수·글자 수)와 `one-line`, `key-points`의 `<li>` 3~7개, `detailed`, `result-meta`(모델명·소요 시간 "초") 표시 |
| AC-F-02 | P0 | `index_pdf.html`을 `file://`로 직접 열기(서버 실행 중) / 파일 선택 버튼으로 업로드 / AC-F-01과 동일 결과(요청이 `http://localhost:8000/api/...`로 감) |
| AC-F-03 | P0 | 서버 미실행 + `file://`로 열기 / 페이지 로드 / `server-banner`에 `NETWORK_ERROR` 메시지 표시 |
| AC-F-04 | P0 | 서버 미실행 / 파일 업로드 시도 / `error-box`에 `NETWORK_ERROR` 메시지 |
| AC-F-05 | P0 | `sample.txt` 선택 / — / 서버 요청 없이 `error-box`에 "PDF 파일만 업로드할 수 있습니다." |
| AC-F-06 | P0 | `big_21mb.pdf` 선택 / — / 서버 요청 없이 `FILE_TOO_LARGE` 메시지 |
| AC-F-07 | P0 | `scanned.pdf` 업로드 / — / `error-box`에 서버 `NO_TEXT` 메시지 그대로 표시 |
| AC-F-08 | P0 | 요약 단계에서 서버가 `AI_RATE_LIMIT` 반환 / "다시 시도" 클릭 / 업로드 재호출 없이 `/api/summarize`만 재호출 |
| AC-F-09 | P0 | 업로드·요약 진행 중 / 다른 파일 드롭 / 무시됨(요청 추가 발생 없음) |
| AC-F-10 | P0 | 결과 표시 상태 / "다른 파일 요약하기" 클릭 / idle 상태로 복귀, 결과·에러 영역 숨김 |
| AC-F-11 | P1 | 결과 표시 상태 / "복사" 클릭 / 클립보드 내용이 §5.5 형식, 버튼 문구가 2초간 "복사되었습니다" |
| AC-F-12 | P1 | 결과 표시 상태 / "다운로드" 클릭 / `text_1p_ko_요약.md` 파일 다운로드, UTF-8, §5.5 형식 |
| AC-F-13 | P0 | 드래그 중 / `drop-zone` 위 진입·이탈 / `dragover` 클래스 추가·제거 |
| AC-F-14 | P0 | 업로드 영역 밖에 PDF 드롭 / — / 브라우저가 PDF를 열지 않음(페이지 유지) |
| AC-F-15 | P1 | 파일 2개 동시 드롭 / — / 첫 파일만 처리 + 안내 문구 표시 |
| AC-F-16 | P0 | 모델 요약에 `<script>alert(1)</script>` 문자열이 포함된 경우(모킹) / 표시 / 스크립트 실행되지 않고 문자열로 표시 |
| AC-F-17 | P0 | 페이지 전체 / 육안 확인 / 모든 UI 문구가 한국어, `notice` 고지 문구 표시 |
| AC-F-18 | P1 | `huge_text.pdf` 업로드 / — / `doc-info` 표시 후 `TEXT_TOO_LONG` 메시지, `/api/summarize` 호출 없음 |
| AC-F-19 | P1 | 폴백 응답(`fallback_used=true`) / 결과 표시 / `result-meta`에 "(대체 모델 사용)" 표시 |

### 6.6 비기능
| ID | P | Given / When / Then |
|----|---|---------------------|
| AC-N-01 | P1 | 10MB 이하 텍스트 PDF / 업로드 10회 / p95 ≤ 3초 |
| AC-N-02 | P0 | 저장소 전체 / `OPENROUTER_API_KEY` 값 검색 / `.env` 외 어떤 파일(HTML, 로그, 테스트 스냅샷)에도 없음 |
| AC-N-03 | P1 | 한글 경로(`C:\한빛미디어_클로드코드\PDF요약`)에서 실행 / 기동 및 `GET /` / 정상 동작 |

---

## 7. 역할별 인계 사항
| 담당 | 구현/확인 사항 |
|------|----------------|
| 백엔드 에이전트 | §1.2 설정, §2 공통 규약(에러 변환 핸들러·CORS), §3.1~3.4 엔드포인트, §4 메시지 문자열 그대로 사용 |
| AI통합 에이전트 | 기본 모델 ID 검증(결과: `deepseek/deepseek-chat-v3.1:free` 없음 → 저가 유료 모델로 대체, §1.2 참고), §3.4.1~3.4.3 프롬프트·파싱·폴백·청크 |
| 프론트엔드 에이전트 | §5 전체, 고정 `id` 유지 |
| QA 에이전트 | §6.0 픽스처 생성, §6 AC를 pytest(+모킹) 및 브라우저 E2E로 변환 |
