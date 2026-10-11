# PDF 요약

PDF 파일을 끌어다 놓으면 AI가 한국어로 요약해 주는 웹 애플리케이션입니다.
FastAPI 백엔드가 PDF에서 텍스트를 추출하고, [OpenRouter](https://openrouter.ai)의 DeepSeek 모델로 요약합니다.

요약 결과는 세 부분으로 나옵니다.

- **한 줄 요약**
- **핵심 포인트** (3~7개)
- **상세 요약**

결과는 복사하거나 Markdown(`.md`) 파일로 내려받을 수 있습니다.

## 빠른 시작

Python 3.11 이상이 필요합니다.

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

`.env`를 열어 OpenRouter API 키를 넣습니다.

```
OPENROUTER_API_KEY=sk-or-...
```

서버를 실행합니다.

```bash
python -m uvicorn backend.main:app --port 8000
```

브라우저에서 http://localhost:8000 에 접속하거나 `index_pdf.html`을 더블클릭해 엽니다.
파일로 직접 열면 페이지가 `http://localhost:8000`으로 요청을 보내므로, 어느 쪽이든 서버는 켜져 있어야 합니다.
서버가 꺼져 있으면 화면 위쪽에 실행 명령어가 안내됩니다.

## 모델

| 구분 | 모델 | 바꾸는 방법 |
|---|---|---|
| 기본 | `deepseek/deepseek-v3.2` | `.env`의 `OPENROUTER_MODEL` |
| 대체 | `deepseek/deepseek-chat-v3.1` | `.env`의 `OPENROUTER_FALLBACK_MODEL` |

- OpenRouter에는 현재 무료 DeepSeek 모델이 없어서 두 모델 모두 유료입니다. 비용은 요약 1회에 매우 적지만 0은 아닙니다.
- 기본 모델이 429, 5xx, 60초 초과, 네트워크 오류, 모델 없음 중 하나로 실패하면 대체 모델로 한 번 다시 시도합니다.
- 인증 오류(401/403)와 잔액 부족(402)일 때는 다시 시도하지 않습니다.
- 지금 쓰이는 모델은 `GET /api/health`로 확인할 수 있습니다.

## 제한

| 항목 | 값 |
|---|---|
| 파일 크기 | 20MB 이하 |
| 페이지 수 | 300쪽 이하 |
| 추출 텍스트 | 300,000자 이하 |
| 요약 1회 제한 시간 | 180초 |

30,000자가 넘는 문서는 조각으로 나눠 각각 요약한 뒤 하나로 합칩니다.
57,000자 문서는 약 30초, 30만 자에 가까운 문서는 약 40초 걸렸습니다.

다음 파일은 명확한 한국어 안내와 함께 거부됩니다.

- 암호가 걸린 PDF
- 손상된 PDF
- 텍스트가 없는 스캔(이미지) PDF
- 확장자만 `.pdf`인 다른 파일

스캔 PDF의 글자 인식(OCR)은 지원하지 않습니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/` | `index_pdf.html` 화면 |
| `GET` | `/api/health` | 서버 상태, API 키 설정 여부, 사용 모델 |
| `POST` | `/api/upload` | PDF 업로드(multipart 필드 `file`). 응답은 `{text, metadata}` |
| `POST` | `/api/summarize` | `{"text": "...", "filename": "선택"}`. 응답은 `{summary: {one_line, key_points, detailed}, model, fallback_used, elapsed_ms, chunk_count}` |

에러는 모두 같은 형식으로 돌아옵니다.

```json
{ "error": { "code": "ENCRYPTED_PDF", "message": "암호가 설정된 PDF는 지원하지 않습니다. ..." } }
```

전체 에러 코드와 메시지는 [docs/FEATURE_SPEC.md](docs/FEATURE_SPEC.md)에 있습니다.

## 테스트

```bash
python -m pytest            # OpenRouter를 모킹한 테스트 (API 키 불필요, 비용 없음)
python -m pytest -m live    # 실제 OpenRouter 호출 테스트 (API 키 필요, 비용 발생)
```

모킹 테스트는 `respx`를 사용하며 `requirements.txt`에 포함되어 있습니다.

테스트용 PDF는 `tests/fixtures/`에 있습니다. 20MB 이상 파일 두 개는 저장소에 넣지 않았고, 테스트를 실행하면 자동으로 다시 만들어집니다.
전체를 다시 만들려면 다음을 실행합니다.

```bash
python tests/make_fixtures.py
```

## 폴더 구조

```
backend/
  main.py           FastAPI 앱, 라우트, 업로드 검증, CORS
  pdf_extractor.py  PyMuPDF 텍스트 추출
  summarizer.py     OpenRouter 호출, 대체 모델, 긴 문서 분할 요약
  errors.py         에러 코드와 한국어 메시지 표
index_pdf.html      화면 (HTML·CSS·JS가 한 파일에 들어 있음)
docs/
  PRD.md            제품 요구사항
  FEATURE_SPEC.md   API·에러·화면 명세와 수용 기준
tests/              pytest 테스트와 테스트용 PDF
.claude/agents/     이 앱을 만든 Claude Code 에이전트 정의
```

## 보안 메모

- `.env`는 `.gitignore`에 포함되어 있으며 API 키는 서버에서만 사용합니다. 브라우저로 전달되거나 로그에 남지 않습니다.
- 업로드한 PDF는 메모리에서 처리하고 저장하지 않습니다.
- CORS가 모든 출처를 허용합니다. `file://`로 연 페이지를 지원하기 위해서이며, 로컬 사용을 전제로 한 설정입니다. 외부에 공개하려면 출처를 제한하고 인증을 추가해야 합니다.
