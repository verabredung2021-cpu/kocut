"""v0.5.1 품질 필터 회귀 테스트."""
from __future__ import annotations

from kocut.quality import get_profile, smooth_cuts
from kocut.types import CutCandidate, CutKind


def _sil(s: float, e: float) -> CutCandidate:
    return CutCandidate(start=s, end=e, kind=CutKind.SILENCE, reason="무음", confidence=0.95)


def _filler(s: float, e: float) -> CutCandidate:
    return CutCandidate(start=s, end=e, kind=CutKind.FILLER, reason="간투사", text="어", confidence=0.9)


def test_quality_drops_short_silence_cuts() -> None:
    cuts = [_sil(1.0, 1.3), _sil(5.0, 6.0)]
    smoothed = smooth_cuts(cuts, total_duration=10.0, min_silence_cut_ms=700)
    assert [(round(c.start, 1), round(c.end, 1)) for c in smoothed] == [(5.0, 6.0)]


def test_quality_keeps_short_filler_cut() -> None:
    cuts = [_filler(1.0, 1.25), _sil(5.0, 5.3)]
    smoothed = smooth_cuts(cuts, total_duration=10.0, min_silence_cut_ms=700)
    assert len(smoothed) == 1
    assert smoothed[0].kind == CutKind.FILLER


def test_quality_smooths_micro_keep_between_silences() -> None:
    # silence - 0.4s spoken island - silence: 양옆 무음 컷을 되살려 점프컷 난사를 막는다.
    cuts = [_sil(1.0, 2.0), _sil(2.4, 3.4), _sil(8.0, 9.0)]
    smoothed = smooth_cuts(
        cuts, total_duration=10.0, min_silence_cut_ms=500, min_keep_between_cuts_ms=1000
    )
    assert [(c.start, c.end) for c in smoothed] == [(8.0, 9.0)]


def test_quality_profiles_exist() -> None:
    assert get_profile("longform").min_silence_cut_ms >= get_profile("tight").min_silence_cut_ms
