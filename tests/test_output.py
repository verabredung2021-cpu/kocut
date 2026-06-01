"""출력 생성 테스트 (SRT / EDL)."""
from __future__ import annotations

from pathlib import Path

from kocut.output import _invert_cuts, _seconds_to_srt, _seconds_to_tc, write_edl, write_srt
from kocut.types import CutCandidate, CutKind, SubtitleSegment


def test_seconds_to_srt_format() -> None:
    assert _seconds_to_srt(0.0) == "00:00:00,000"
    assert _seconds_to_srt(1.5) == "00:00:01,500"
    assert _seconds_to_srt(3661.25) == "01:01:01,250"


def test_seconds_to_tc_format() -> None:
    assert _seconds_to_tc(0.0, 30) == "00:00:00:00"
    assert _seconds_to_tc(1.0, 30) == "00:00:01:00"
    # 0.5초 @ 30fps = 15프레임
    assert _seconds_to_tc(0.5, 30) == "00:00:00:15"


def test_write_srt(tmp_path: Path) -> None:
    subs = [
        SubtitleSegment(index=1, start=0.0, end=1.5, text="안녕하세요"),
        SubtitleSegment(index=2, start=2.0, end=3.5, text="반갑습니다"),
    ]
    out = tmp_path / "test.srt"
    write_srt(subs, out)
    content = out.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:01,500\n안녕하세요" in content
    assert "2\n00:00:02,000 --> 00:00:03,500\n반갑습니다" in content


def test_invert_cuts_basic() -> None:
    # 10초 영상, 2~4초와 6~8초를 컷 → 남길 구간: 0-2, 4-6, 8-10
    cuts = [
        CutCandidate(start=2.0, end=4.0, kind=CutKind.SILENCE, reason="무음"),
        CutCandidate(start=6.0, end=8.0, kind=CutKind.FILLER, reason="간투사"),
    ]
    keep = _invert_cuts(cuts, total_duration=10.0)
    assert keep == [(0.0, 2.0), (4.0, 6.0), (8.0, 10.0)]


def test_invert_cuts_overlapping() -> None:
    # 겹치는 컷은 병합되어야 함
    cuts = [
        CutCandidate(start=2.0, end=5.0, kind=CutKind.SILENCE, reason="무음"),
        CutCandidate(start=4.0, end=7.0, kind=CutKind.FILLER, reason="간투사"),
    ]
    keep = _invert_cuts(cuts, total_duration=10.0)
    assert keep == [(0.0, 2.0), (7.0, 10.0)]


def test_invert_cuts_empty() -> None:
    # 컷이 없으면 전체가 남을 구간
    keep = _invert_cuts([], total_duration=10.0)
    assert keep == [(0.0, 10.0)]


def test_write_edl(tmp_path: Path) -> None:
    cuts = [CutCandidate(start=2.0, end=4.0, kind=CutKind.SILENCE, reason="무음")]
    out = tmp_path / "test.edl"
    write_edl(cuts, out, total_duration=10.0, fps=30.0)
    content = out.read_text(encoding="utf-8")
    assert "TITLE: KoCut Edit" in content
    assert "FCM: NON-DROP FRAME" in content
    # 두 개의 남길 구간 (0-2, 4-10) → 이벤트 2개
    assert "001" in content
    assert "002" in content


def test_seconds_to_tc_fps_zero() -> None:
    # fps=0 이면 ZeroDivisionError 대신 30fps로 보정
    assert _seconds_to_tc(1.5, 0) == "00:00:01:15"


def test_seconds_to_tc_fps_negative() -> None:
    # 음수 fps → 음수 프레임이 나오면 안 됨
    frames = _seconds_to_tc(1.5, -30).split(":")[-1]
    assert "-" not in frames
    assert int(frames) >= 0


def test_seconds_to_tc_fps_nan() -> None:
    import math

    assert _seconds_to_tc(1.5, math.nan) == "00:00:01:15"


def test_write_edl_fps_zero_no_crash(tmp_path: Path) -> None:
    cuts = [CutCandidate(start=1.0, end=2.0, kind=CutKind.SILENCE, reason="x")]
    out = tmp_path / "fps0.edl"
    write_edl(cuts, out, total_duration=10.0, fps=0)
    assert out.exists()


def test_srt_strips_whitespace(tmp_path: Path) -> None:
    # 자막 앞뒤 공백/탭은 제거되어야 함
    subs = [SubtitleSegment(index=1, start=0.0, end=1.0, text="  \t앞뒤공백\t  ")]
    out = tmp_path / "ws.srt"
    write_srt(subs, out)
    lines = out.read_text(encoding="utf-8").split("\n")
    assert lines[2] == "앞뒤공백"
