"""앱 공통 예외. 모든 에러는 {"error": {"code", "message"}} 형식으로 응답된다.

메시지·HTTP 상태는 docs/FEATURE_SPEC.md §4 에러 코드 표와 정확히 일치해야 한다.
"""

ERRORS: dict[str, tuple[int, str]] = {
    "NO_FILE": (400, "업로드할 파일이 없습니다. PDF 파일을 선택해 주세요."),
    "INVALID_FILE_TYPE": (400, "PDF 파일만 업로드할 수 있습니다."),
    "EMPTY_FILE": (400, "빈 파일입니다. 내용이 있는 PDF를 업로드해 주세요."),
    "FILE_TOO_LARGE": (413, "파일 크기가 20MB를 초과합니다. 20MB 이하의 PDF를 업로드해 주세요."),
    "ENCRYPTED_PDF": (422, "암호가 설정된 PDF는 지원하지 않습니다. 암호를 해제한 뒤 다시 업로드해 주세요."),
    "CORRUPTED_PDF": (422, "PDF 파일이 손상되어 열 수 없습니다. 다른 파일로 시도해 주세요."),
    "TOO_MANY_PAGES": (413, "페이지 수가 너무 많습니다. 300페이지 이하의 PDF를 업로드해 주세요."),
    "NO_TEXT": (422, "PDF에서 텍스트를 추출할 수 없습니다. 스캔된 이미지 PDF는 지원하지 않습니다."),
    "INVALID_REQUEST": (400, "요청 형식이 올바르지 않습니다."),
    "EMPTY_TEXT": (400, "요약할 텍스트가 비어 있습니다."),
    "TEXT_TOO_LONG": (413, "문서가 너무 길어 요약할 수 없습니다. (최대 300,000자)"),
    "API_KEY_MISSING": (500, "서버에 AI API 키가 설정되지 않았습니다. 관리자에게 문의하세요. (.env의 OPENROUTER_API_KEY 확인)"),
    "AI_AUTH_ERROR": (502, "AI 서비스 인증에 실패했습니다. API 키를 확인해 주세요."),
    "AI_TIMEOUT": (504, "AI 요약 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."),
    "AI_RATE_LIMIT": (429, "AI 서비스 요청이 많아 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."),
    "AI_INVALID_RESPONSE": (502, "AI 응답을 해석하지 못했습니다. 다시 시도해 주세요."),
    "AI_ERROR": (502, "AI 요약 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."),
    "NOT_FOUND": (404, "요청한 경로를 찾을 수 없습니다."),
    "METHOD_NOT_ALLOWED": (405, "허용되지 않은 요청 방식입니다."),
    "INTERNAL_ERROR": (500, "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."),
}


class AppError(Exception):
    """AppError("NO_TEXT") 처럼 코드만 주면 §4 표의 상태·메시지를 사용한다."""

    def __init__(self, code: str, message: str | None = None, status: int | None = None):
        default_status, default_message = ERRORS.get(code, ERRORS["INTERNAL_ERROR"])
        self.code = code
        self.message = message or default_message
        self.status = status or default_status
        super().__init__(self.message)
