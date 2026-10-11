"""테스트용 PDF 픽스처 생성기 (PyMuPDF).

사용: 프로젝트 루트에서 `python tests/make_fixtures.py [--force]`
생성 위치: tests/fixtures/  (FEATURE_SPEC §6.0 + QA 역할 정의의 10종)
대용량 파일(big_21mb.pdf, exact_20mb.pdf)은 .gitignore 대상이며 conftest가 없으면 자동 생성한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MAX_BYTES = 20 * 1024 * 1024
KO_FONT = "korea"  # PyMuPDF 내장 CJK 폰트

KO_PARA = (
    "본 보고서는 2026년 국내 전기차 시장의 성장 전망을 분석한다. 판매량은 전년 대비 25% 증가한 약 40만 대로 예상되며, "
    "충전 인프라 확충과 배터리 가격 하락이 성장의 핵심 요인으로 꼽힌다. 반면 정부 보조금 축소는 중저가 모델 간 경쟁을 "
    "촉진하고, 완성차 업체들은 가격 인하와 신모델 출시로 대응하고 있다. 지역별로는 수도권의 보급률이 가장 높고, "
    "지방 도시의 충전기 부족 문제가 여전히 과제로 남아 있다. 보고서는 충전 사업자와 지자체의 협력 모델을 제안한다. "
)
EN_PARA = (
    "The quarterly report reviews the performance of the renewable energy division. Revenue grew 18 percent "
    "year over year, driven by solar installations in Southeast Asia and new offshore wind contracts in Europe. "
    "Operating margins narrowed slightly because of higher component costs and logistics delays. Management "
    "expects supply chain pressure to ease in the second half and plans to expand battery storage capacity. "
)


def _ko_textbox(page: pymupdf.Page, text: str, fontsize: float = 10, rect=None) -> None:
    rect = rect or pymupdf.Rect(40, 40, page.rect.width - 40, page.rect.height - 40)
    rc = page.insert_textbox(rect, text, fontsize=fontsize, fontname=KO_FONT)
    if rc < 0:
        raise RuntimeError(f"텍스트가 페이지에 다 들어가지 않음 (rc={rc})")


def _save(doc: pymupdf.Document, name: str, **kw) -> Path:
    path = FIXTURES / name
    doc.save(path, garbage=3, deflate=True, **kw)
    doc.close()
    return path


def text_1p_en() -> None:
    doc = pymupdf.open()
    _ko_textbox(doc.new_page(), "Quarterly Report\n\n" + EN_PARA * 3, 11)
    _save(doc, "text_1p_en.pdf")


def text_1p_ko() -> None:
    doc = pymupdf.open()
    _ko_textbox(doc.new_page(), "전기차 시장 보고서\n\n" + KO_PARA * 4, 11)  # ~1,000자
    _save(doc, "text_1p_ko.pdf")  # 메타데이터 없음 (AC-U-15)


def text_multi_mixed() -> None:
    doc = pymupdf.open()
    for n in range(1, 6):
        _ko_textbox(doc.new_page(), f"PAGE-{n}\n\n" + KO_PARA + "\n\n" + EN_PARA, 11)
    doc.set_metadata({"title": "혼합 문서 테스트", "author": "홍길동"})
    _save(doc, "text_multi_mixed.pdf")


def long_text(pages: int = 60) -> None:
    """50페이지 이상, 추출 텍스트 30,000자 초과 ~ 300,000자 이하 (청크 분할 검증)."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        body = f"제{n}장 시장 분석 {n}\n\n" + KO_PARA.replace("2026년", f"{2000 + n}년") * 2 + "\n\n" + EN_PARA
        _ko_textbox(doc.new_page(), body, 9)
    _save(doc, "long_text.pdf")


def huge_text() -> None:
    """추출 텍스트 300,000자 초과, 300페이지 이하, 20MB 이하."""
    doc = pymupdf.open()
    for n in range(1, 121):
        _ko_textbox(doc.new_page(), f"[{n}] " + EN_PARA * 7, 6.5,
                    pymupdf.Rect(20, 20, 575, 822))
    _save(doc, "huge_text.pdf")


def table_layout() -> None:
    """표 + 2단 레이아웃."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 50), "지역별 판매 실적 (표)", fontsize=14, fontname=KO_FONT)
    rows = [("지역", "1분기", "2분기", "증감"), ("서울", "1,200", "1,450", "+20.8%"),
            ("부산", "640", "700", "+9.4%"), ("대구", "410", "395", "-3.7%")]
    x0, y0, cw, rh = 40, 70, 120, 24
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            page.insert_textbox(rect + (4, 5, 0, 0), cell, fontsize=10, fontname=KO_FONT)
    _ko_textbox(page, "왼쪽 단: " + KO_PARA, 9, pymupdf.Rect(40, 200, 290, 800))
    _ko_textbox(page, "오른쪽 단: " + EN_PARA * 2, 9, pymupdf.Rect(305, 200, 555, 800))
    _save(doc, "table_layout.pdf")


def encrypted() -> None:
    doc = pymupdf.open()
    _ko_textbox(doc.new_page(), "비밀 문서\n\n" + KO_PARA, 11)
    _save(doc, "encrypted.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
          owner_pw="owner-secret", user_pw="user-secret")


def scanned() -> None:
    """텍스트 레이어 없이 이미지만 있는 PDF (텍스트 페이지를 렌더링한 이미지 삽입)."""
    src = pymupdf.open()
    _ko_textbox(src.new_page(), "스캔 문서\n\n" + KO_PARA * 2, 12)
    pix = src[0].get_pixmap(dpi=100)
    src.close()
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pix)
    _save(doc, "scanned.pdf")


def corrupted() -> None:
    # %PDF- 헤더는 있으나 나머지는 깨진 파일
    (FIXTURES / "corrupted.pdf").write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 8 + b"\n%%garbage")
    # 정상 PDF의 앞부분만 남긴(잘린) 파일
    good = (FIXTURES / "text_1p_ko.pdf").read_bytes()
    (FIXTURES / "truncated.pdf").write_bytes(good[: len(good) // 3])


def fake_and_txt() -> None:
    (FIXTURES / "fake.pdf").write_text("이것은 PDF가 아닌 일반 텍스트 파일입니다.\nplain text\n", encoding="utf-8")
    (FIXTURES / "sample.txt").write_text("일반 텍스트 파일입니다. sample text file.\n", encoding="utf-8")


def empty_files() -> None:
    (FIXTURES / "empty.pdf").write_bytes(b"")
    doc = pymupdf.open()
    doc.new_page()  # 빈 페이지 1장
    _save(doc, "blank_page.pdf")


def big_21mb() -> None:
    """20MB 초과 (헤더만 PDF, 서버는 크기에서 먼저 거부)."""
    with open(FIXTURES / "big_21mb.pdf", "wb") as f:
        f.write(b"%PDF-1.7\n%")
        f.write(b"0" * (MAX_BYTES + 1024 * 1024))


def exact_20mb() -> None:
    """정확히 20,971,520바이트의 유효 PDF (%%EOF 뒤 주석 패딩)."""
    base = (FIXTURES / "text_1p_ko.pdf").read_bytes()
    pad = MAX_BYTES - len(base)
    data = base + b"\n%" + b" " * (pad - 3) + b"\n"
    assert len(data) == MAX_BYTES
    (FIXTURES / "exact_20mb.pdf").write_bytes(data)


def pages_301() -> None:
    doc = pymupdf.open()
    for n in range(1, 302):
        doc.new_page().insert_text((50, 72), f"Page {n} of the oversized document.", fontsize=12)
    _save(doc, "pages_301.pdf")


def upper_and_special() -> None:
    base = (FIXTURES / "text_1p_en.pdf").read_bytes()
    (FIXTURES / "UPPER.PDF").write_bytes(base)
    (FIXTURES / "보고서 #1 (최종)&'특수 문자.pdf").write_bytes((FIXTURES / "text_1p_ko.pdf").read_bytes())


BUILDERS = [text_1p_en, text_1p_ko, text_multi_mixed, long_text, huge_text, table_layout, encrypted,
            scanned, corrupted, fake_and_txt, empty_files, big_21mb, exact_20mb, pages_301,
            upper_and_special]


def main(force: bool = False) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    marker = FIXTURES / "exact_20mb.pdf"
    if marker.exists() and (FIXTURES / "UPPER.PDF").exists() and not force:
        return
    for b in BUILDERS:
        b()


if __name__ == "__main__":
    main(force="--force" in sys.argv)
    for p in sorted(FIXTURES.iterdir()):
        print(f"{p.stat().st_size:>10,}  {p.name}")
