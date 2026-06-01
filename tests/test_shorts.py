"""쇼츠 후보 점수 테스트."""
from __future__ import annotations

from kocut.shorts import score_shorts_candidates
from kocut.types import Segment


def _seg(text: str, start: float, end: float) -> Segment:
    return Segment(start=start, end=end, text=text, words=[])


def test_empty_input() -> None:
    assert score_shorts_candidates([]) == []


def test_hook_keywords_score_higher() -> None:
    # 훅 키워드가 많은 구간
    segments = [
        _seg("결론부터 말하면 이게 진짜 핵심이고 가장 중요한 방법입니다 이유를 설명할게요", 0.0, 30.0),
        _seg("그냥 평범한 이야기를 길게 늘어놓는 부분이에요 별다른 내용은 없어요", 30.0, 60.0),
    ]
    candidates = score_shorts_candidates(segments, target_count=2, window_s=30, step_s=30, min_duration_s=20)
    assert len(candidates) >= 1
    # 첫 구간(훅 많음)이 후보에 포함
    first = [c for c in candidates if c.start < 15]
    assert len(first) >= 1


def test_candidates_within_duration_limit() -> None:
    segments = [_seg("진짜 대박 충격적인 결론 핵심 비밀 꿀팁", i * 10.0, i * 10.0 + 10.0) for i in range(10)]
    candidates = score_shorts_candidates(segments, max_duration_s=60)
    for cand in candidates:
        assert cand.duration <= 60.5


def test_no_overlap_in_results() -> None:
    segments = [_seg("진짜 핵심 결론 대박 충격 소름 비밀", i * 5.0, i * 5.0 + 5.0) for i in range(30)]
    candidates = score_shorts_candidates(segments, target_count=5)
    # 결과 구간들은 서로 겹치지 않아야 함
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            assert not (a.end > b.start and a.start < b.end)


def test_sorted_by_start() -> None:
    segments = [_seg("진짜 대박 핵심 결론입니다", i * 15.0, i * 15.0 + 15.0) for i in range(8)]
    candidates = score_shorts_candidates(segments)
    starts = [c.start for c in candidates]
    assert starts == sorted(starts)


def test_step_zero_no_infinite_loop() -> None:
    # step_s=0 이면 무한루프였던 버그 — 가드로 즉시 반환해야 함
    segments = [_seg("진짜 핵심 결론입니다", 0.0, 30.0)]
    result = score_shorts_candidates(segments, step_s=0)
    assert isinstance(result, list)


def test_negative_params_no_crash() -> None:
    segments = [_seg("핵심 대박 결론", 0.0, 30.0)]
    result = score_shorts_candidates(segments, step_s=-5, window_s=-1, max_duration_s=-1)
    assert isinstance(result, list)
