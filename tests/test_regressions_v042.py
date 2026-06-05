"""v0.4.2 회귀 테스트 — 자막 텍스트 정리 (소수점 공백 등)."""
from __future__ import annotations

from kocut.subtitles import _clean_text, split_subtitles
from kocut.types import Word


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


def test_clean_text_fixes_decimal_spacing() -> None:
    # Whisper가 "0.5"를 "0 .5"로 뱉는 흔한 오류
    assert _clean_text("그게 0 .5인가") == "그게 0.5인가"
    assert _clean_text("1 . 2가 나온") == "1.2가 나온"
    # 공백 정리
    assert _clean_text("안녕  하세요") == "안녕 하세요"


def test_clean_text_preserves_sentence_boundary() -> None:
    # 마침표 '앞' 공백이 없으면(문장 경계) 건드리지 않아야 함
    assert _clean_text("2025. 5월에") == "2025. 5월에"


def test_split_subtitles_applies_cleanup() -> None:
    words = [_w("그게", 0.0, 0.4), _w("0", 0.5, 0.6), _w(".5인가?", 0.65, 1.0)]
    subs = split_subtitles(words)
    assert subs
    joined = " ".join(s.text for s in subs)
    assert "0.5인가?" in joined
    assert "0 .5" not in joined
