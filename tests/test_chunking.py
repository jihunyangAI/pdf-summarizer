"""청크 분할 단위 테스트 — FEATURE_SPEC §3.4.3."""
import re

from backend.summarizer import CHUNK_CHARS, _parse_summary, _split_chunks


def _squash(s):
    return re.sub(r"\s", "", s)


def test_short_text_single_chunk():
    assert _split_chunks("짧은 문서입니다.") == ["짧은 문서입니다."]


def test_chunks_within_limit_and_lossless():
    text = "\n\n".join(f"문단 {i}. " + "내용 문장입니다. " * 50 for i in range(300))
    chunks = _split_chunks(text)
    assert len(chunks) >= 2
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    assert _squash("".join(chunks)) == _squash(text)


def test_prefers_paragraph_boundary():
    para = "가" * 20_000
    text = para + "\n\n" + para + "\n\n" + para
    chunks = _split_chunks(text)
    assert chunks == [para, para, para]


def test_falls_back_to_line_then_sentence():
    line = "나" * 25_000
    assert _split_chunks(line + "\n" + line) == [line, line]
    sent = "다" * 24_999 + "다."
    chunks = _split_chunks(sent + " " + sent)
    assert chunks[0] == sent


def test_forced_cut_without_separators():
    text = "x" * 75_000
    chunks = _split_chunks(text)
    assert [len(c) for c in chunks] == [30_000, 30_000, 15_000]


def test_300k_max_is_about_10_chunks():
    text = ("문장입니다. " * 60_000)[:300_000]
    chunks = _split_chunks(text)
    assert 10 <= len(chunks) <= 11
    assert all(len(c) <= CHUNK_CHARS for c in chunks)


def test_parse_summary_trailing_comma_and_prose():
    raw = '결과입니다:\n{"one_line": "요약", "key_points": ["a", "b", "c",], "detailed": "d",}\n감사합니다.'
    s = _parse_summary(raw)
    assert s["key_points"] == ["a", "b", "c"]


def test_parse_summary_key_points_as_string_lines():
    raw = '{"one_line": "요약", "key_points": "- a\\n- b\\n- c", "detailed": "d"}'
    assert _parse_summary(raw)["key_points"] == ["a", "b", "c"]
