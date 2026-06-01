"""간투사 검출 테스트."""
from __future__ import annotations

from kocut.fillers import detect_fillers
from kocut.types import Word


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


def test_empty_input() -> None:
    assert detect_fillers([]) == []


def test_detects_eum_and_eo() -> None:
    words = [
        _w("음", 0.0, 0.3),
        _w("오늘은", 0.5, 1.0),
        _w("어", 1.2, 1.4),
        _w("중요한", 1.6, 2.1),
    ]
    cuts = detect_fillers(words)
    texts = [c.text for c in cuts]
    assert "음" in texts
    assert "어" in texts
    # 실제 단어는 컷되지 않음
    assert "오늘은" not in texts
    assert "중요한" not in texts


def test_all_cuts_are_filler_kind() -> None:
    words = [_w("그", 0.0, 0.3), _w("뭐", 0.5, 0.8)]
    cuts = detect_fillers(words)
    for cut in cuts:
        assert cut.kind == "filler"


def test_question_eo_excluded() -> None:
    # '어?' 처럼 의문 부호로 끝나면 간투사가 아님
    words = [_w("어?", 0.0, 0.4)]
    cuts = detect_fillers(words)
    assert cuts == []


def test_long_word_not_filler() -> None:
    # '그' 발화가 너무 길면 간투사로 보지 않음
    words = [_w("그", 0.0, 1.5)]
    cuts = detect_fillers(words)
    assert cuts == []


def test_padding_applied() -> None:
    words = [_w("음", 1.0, 1.3)]
    cuts = detect_fillers(words, padding=0.1)
    assert len(cuts) == 1
    assert cuts[0].start < 1.0
    assert cuts[0].end > 1.3


def test_negative_padding_clamped() -> None:
    # 음수 padding이 들어와도 유효한 컷이 나와야 함
    cuts = detect_fillers([_w("음", 1.0, 1.3)], padding=-0.5)
    assert len(cuts) == 1
    assert cuts[0].start >= 0.0
    assert cuts[0].end >= cuts[0].start
