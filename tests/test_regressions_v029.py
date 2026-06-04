"""v0.2.9 회귀 테스트.

EDL relink 정확도(분수 fps), DaVinci용 SOURCE FILE 라인, 마이크로 클립 제거를
검증합니다.
"""
from __future__ import annotations

from pathlib import Path

from kocut.output import _invert_cuts, _seconds_to_tc, write_edl
from kocut.types import CutCandidate


def test_fractional_fps_uses_real_rate_for_timecode() -> None:
    # 23.976 영상: 1000초의 실제 프레임은 round(1000*23.976)=23976 → 00:16:39:00.
    # 24로 반올림해서 프레임까지 세던 이전 코드는 00:16:40:00(=24프레임 밀림)이었다.
    assert _seconds_to_tc(1000.0, 23.976) == "00:16:39:00"
    assert _seconds_to_tc(600.0, 23.976) == "00:09:59:10"
    # 정수 fps는 동작 변화 없음
    assert _seconds_to_tc(1000.0, 30.0) == "00:16:40:00"
    assert _seconds_to_tc(1.0, 25.0) == "00:00:01:00"


def test_degenerate_fps_still_safe() -> None:
    # 0에 가까운/비정상 fps는 베이스(30)로 대체 — 기존 가드 유지.
    assert _seconds_to_tc(1.0, 0.1) == "00:00:01:00"
    assert _seconds_to_tc(1.0, 0.0) == "00:00:01:00"


def test_write_edl_includes_source_file_for_relink(tmp_path: Path) -> None:
    out = tmp_path / "clip.edl"
    write_edl([], out, total_duration=2.0, fps=30.0, source_name="C0430.mp4")
    text = out.read_text(encoding="utf-8")
    # Premiere(FROM CLIP NAME) + DaVinci(SOURCE FILE) 둘 다 relink 가능하도록
    assert "* FROM CLIP NAME: C0430.mp4" in text
    assert "* SOURCE FILE: C0430.mp4" in text


def test_invert_cuts_drops_micro_keep() -> None:
    # [0,1]과 [1.05,2.0] 두 컷 사이에 0.05초짜리 남길 조각이 생긴다.
    cuts = [
        CutCandidate(start=0.0, end=1.0, kind="silence", reason=""),
        CutCandidate(start=1.05, end=2.0, kind="silence", reason=""),
    ]
    # min_keep=0이면 0.05초 조각이 남고
    assert _invert_cuts(cuts, total_duration=2.0) == [(1.0, 1.05)]
    # min_keep=0.2면 버려진다
    assert _invert_cuts(cuts, total_duration=2.0, min_keep=0.2) == []


def test_write_edl_micro_clip_floor_removes_event(tmp_path: Path) -> None:
    cuts = [
        CutCandidate(start=0.0, end=1.0, kind="silence", reason=""),
        CutCandidate(start=1.05, end=2.0, kind="silence", reason=""),
    ]
    out = tmp_path / "clip.edl"
    write_edl(cuts, out, total_duration=2.0, fps=30.0, min_clip_seconds=0.2)
    text = out.read_text(encoding="utf-8")
    # 유일한 남길 구간(0.05초)이 제거되어 편집 이벤트 줄이 없어야 한다.
    assert " V     C " not in text
    assert " A     C " not in text
