"""POST /api/upload — FEATURE_SPEC §3.3, AC-U-01 ~ AC-U-16."""
import os
import re
import tempfile

import pytest

from tests.conftest import FIXTURES, assert_error, fixture_bytes


def upload(client, name, data=None, field="file", ctype="application/pdf"):
    data = fixture_bytes(name) if data is None else data
    return client.post("/api/upload", files={field: (name, data, ctype)})


# ---------------------------------------------------------------- 성공 케이스

def test_ac_u01_korean_single_page(client):
    data = fixture_bytes("text_1p_ko.pdf")
    r = upload(client, "text_1p_ko.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"text", "metadata"}  # §3.3 응답 형식
    m = body["metadata"]
    assert set(m.keys()) == {"filename", "size_bytes", "page_count", "char_count", "title", "author"}
    assert "본 보고서는 2026년 국내 전기차 시장의 성장 전망을 분석한다." in body["text"]
    assert m["page_count"] == 1
    assert m["char_count"] == len(body["text"])
    assert m["filename"] == "text_1p_ko.pdf"
    assert m["size_bytes"] == len(data)


def test_ac_u02_multi_page_mixed_order(client):
    r = upload(client, "text_multi_mixed.pdf")
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert r.json()["metadata"]["page_count"] == 5
    positions = [text.index(f"PAGE-{n}") for n in range(1, 6)]
    assert positions == sorted(positions)
    assert "renewable energy" in text and "전기차" in text
    # 페이지 사이 \n\n
    for n in range(2, 6):
        assert f"\n\nPAGE-{n}" in text


def test_ac_u02_metadata_title_author(client):
    m = upload(client, "text_multi_mixed.pdf").json()["metadata"]
    assert m["title"] == "혼합 문서 테스트"
    assert m["author"] == "홍길동"


def test_ac_u15_no_metadata_is_null(client):
    m = upload(client, "text_1p_ko.pdf").json()["metadata"]
    assert m["title"] is None and m["author"] is None


def test_english_single_page(client):
    r = upload(client, "text_1p_en.pdf")
    assert r.status_code == 200
    assert "Quarterly Report" in r.json()["text"]


def test_table_and_two_column_layout(client):
    r = upload(client, "table_layout.pdf")
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    for cell in ("지역", "서울", "1,450", "+20.8%", "-3.7%", "왼쪽 단", "오른쪽 단"):
        assert cell in text, cell


def test_long_document_50plus_pages(client):
    r = upload(client, "long_text.pdf")
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["page_count"] >= 50
    assert 30_000 < m["char_count"] <= 300_000


def test_huge_text_upload_still_succeeds(client):
    """§3.3 참고: char_count > 300,000이어도 업로드는 성공."""
    r = upload(client, "huge_text.pdf")
    assert r.status_code == 200
    assert r.json()["metadata"]["char_count"] > 300_000


def test_ac_u13_uppercase_extension(client):
    assert upload(client, "UPPER.PDF").status_code == 200


def test_special_korean_filename(client):
    name = "보고서 #1 (최종)&'특수 문자.pdf"
    r = upload(client, name)
    assert r.status_code == 200, r.text
    assert r.json()["metadata"]["filename"] == name


def test_filename_path_is_stripped(client):
    r = client.post("/api/upload", files={"file": ("C:\\Users\\x\\docs\\a.pdf", fixture_bytes("text_1p_en.pdf"))})
    assert r.status_code == 200
    assert r.json()["metadata"]["filename"] == "a.pdf"
    r = client.post("/api/upload", files={"file": ("../../etc/b.pdf", fixture_bytes("text_1p_en.pdf"))})
    assert r.json()["metadata"]["filename"] == "b.pdf"


def test_content_type_header_is_ignored(client):
    r = upload(client, "text_1p_en.pdf", ctype="application/octet-stream")
    assert r.status_code == 200


def test_ac_u10_exact_20mb_allowed(client):
    r = upload(client, "exact_20mb.pdf")
    assert r.status_code != 413
    assert r.status_code == 200, r.text
    assert r.json()["metadata"]["size_bytes"] == 20 * 1024 * 1024


def test_extraction_rules(client):
    """§3.3: 각 줄 오른쪽 공백 제거, 3개 이상 연속 빈 줄 → 2개, 그 외 변형 금지."""
    text = upload(client, "long_text.pdf").json()["text"]
    assert not re.search(r"[ \t]+\n", text)
    assert "\n\n\n\n" not in text


# ---------------------------------------------------------------- 에러 케이스

@pytest.mark.parametrize("name,code", [
    ("scanned.pdf", "NO_TEXT"),            # AC-U-03
    ("blank_page.pdf", "NO_TEXT"),
    ("encrypted.pdf", "ENCRYPTED_PDF"),    # AC-U-04
    ("corrupted.pdf", "CORRUPTED_PDF"),    # AC-U-05
    ("truncated.pdf", "CORRUPTED_PDF"),
    ("fake.pdf", "INVALID_FILE_TYPE"),     # AC-U-06
    ("sample.txt", "INVALID_FILE_TYPE"),   # AC-U-07
    ("empty.pdf", "EMPTY_FILE"),           # AC-U-08
    ("big_21mb.pdf", "FILE_TOO_LARGE"),    # AC-U-09
    ("pages_301.pdf", "TOO_MANY_PAGES"),   # AC-U-12
])
def test_upload_errors(client, name, code):
    assert_error(upload(client, name), code)  # AC-U-16 포함


def test_ac_u11_wrong_field_name(client):
    assert_error(upload(client, "text_1p_en.pdf", field="pdf"), "NO_FILE")


def test_ac_u11_no_file_at_all(client):
    assert_error(client.post("/api/upload"), "NO_FILE")
    assert_error(client.post("/api/upload", data={"other": "x"}), "NO_FILE")


def test_file_field_as_plain_string(client):
    assert_error(client.post("/api/upload", data={"file": "not a file"}), "NO_FILE")


def test_validation_order_empty_before_type(client):
    """§3.3 검증 순서: 0바이트(EMPTY_FILE)가 확장자 검사보다 먼저."""
    assert_error(upload(client, "empty.txt", data=b"", ctype="text/plain"), "EMPTY_FILE")


def test_validation_order_size_before_type(client):
    """§3.3 검증 순서: 크기 초과가 확장자 검사보다 먼저."""
    assert_error(upload(client, "movie.mp4", data=b"0" * (20 * 1024 * 1024 + 1)), "FILE_TOO_LARGE")


def test_one_byte_over_limit(client):
    assert_error(upload(client, "x.pdf", data=b"%PDF-1.7\n" + b"0" * (20 * 1024 * 1024 - 8)), "FILE_TOO_LARGE")


def test_pdf_extension_without_signature(client):
    assert_error(upload(client, "x.pdf", data=b"hello world" * 100), "INVALID_FILE_TYPE")


def test_pdf_signature_with_wrong_extension(client):
    assert_error(upload(client, "x.txt", data=fixture_bytes("text_1p_en.pdf")), "INVALID_FILE_TYPE")


def test_signature_after_1024_bytes_rejected(client):
    assert_error(upload(client, "x.pdf", data=b" " * 1100 + fixture_bytes("text_1p_en.pdf")), "INVALID_FILE_TYPE")


# ---------------------------------------------------------------- 디스크 저장 금지 (AC-U-14)

def _snapshot(dirs):
    out = set()
    for d in dirs:
        for root, _, files in os.walk(d):
            if ".pytest_cache" in root or "__pycache__" in root:
                continue
            for f in files:
                out.add(os.path.join(root, f))
    return out


def test_ac_u14_upload_not_persisted(client):
    project = str(FIXTURES.parent.parent)
    before_proj = _snapshot([project])
    tmp = tempfile.gettempdir()
    before_tmp = {f for f in os.listdir(tmp)}
    assert upload(client, "exact_20mb.pdf").status_code == 200
    assert upload(client, "text_1p_ko.pdf").status_code == 200
    assert _snapshot([project]) - before_proj == set()
    leftover = [f for f in set(os.listdir(tmp)) - before_tmp
                if os.path.isfile(os.path.join(tmp, f)) and os.path.getsize(os.path.join(tmp, f)) > 1_000_000]
    assert leftover == []
