"""재촬영/NG 검출 테스트."""
from __future__ import annotations

from kocut.retakes import detect_retakes
from kocut.types import Segment


def _seg(text: str, start: float, end: float) -> Segment:
    return Segment(start=start, end=end, text=text, words=[])


def test_empty_input() -> None:
    assert detect_retakes([]) == []


def test_detects_ng_marker() -> None:
    segments = [
        _seg("이 부분 설명할게요", 0.0, 2.0),
        _seg("아 잠깐만 다시 할게요", 2.0, 4.0),
        _seg("이 부분 설명하겠습니다", 4.0, 6.0),
    ]
    cuts = detect_retakes(segments)
    reasons = " ".join(c.reason for c in cuts)
    assert "다시" in reasons or "잠깐" in reasons


def test_detects_repeated_utterance() -> None:
    segments = [
        _seg("오늘 날씨가 정말 좋네요", 0.0, 2.0),
        _seg("오늘 날씨가 정말 좋습니다", 2.5, 4.5),
    ]
    cuts = detect_retakes(segments)
    # 유사한 인접 발화 → 앞쪽이 재촬영 후보
    assert len(cuts) >= 1
    assert any("유사" in c.reason or "반복" in c.reason for c in cuts)


def test_all_cuts_are_retake_kind() -> None:
    segments = [
        _seg("다시 갈게요", 0.0, 1.0),
        _seg("본 내용입니다", 1.0, 2.0),
    ]
    cuts = detect_retakes(segments)
    for cut in cuts:
        assert cut.kind == "retake"


def test_dissimilar_segments_not_flagged() -> None:
    segments = [
        _seg("첫 번째 주제는 운동입니다", 0.0, 2.0),
        _seg("두 번째 주제는 식단이에요", 2.0, 4.0),
    ]
    cuts = detect_retakes(segments)
    # 서로 다른 내용이므로 반복으로 잡히지 않음
    repeat_cuts = [c for c in cuts if "유사" in c.reason or "반복" in c.reason]
    assert len(repeat_cuts) == 0


def test_sorted_by_start() -> None:
    segments = [
        _seg("다시 할게요", 0.0, 1.0),
        _seg("내용 하나", 1.0, 2.0),
        _seg("NG 컷", 2.0, 3.0),
    ]
    cuts = detect_retakes(segments)
    starts = [c.start for c in cuts]
    assert starts == sorted(starts)
