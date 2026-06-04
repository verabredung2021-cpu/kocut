"""v0.3.0 회귀 테스트.

단어 경계 컷 보정(말 앞뒤 씹힘 방지)과 FCPXML 출력을 검증합니다.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from kocut.fcpxml import _frame_duration, write_fcpxml
from kocut.refine import refine_cuts
from kocut.types import CutCandidate, Word


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


# ---- 단어 경계 보정 ----

def test_refine_prevents_clipping_next_word() -> None:
    # 간투사 '음' 컷이 패딩으로 1.38까지 확장돼 다음 단어('오늘은', 1.35~)를 침범한다.
    words = [_w("음", 1.0, 1.3), _w("오늘은", 1.35, 1.9)]
    cut = CutCandidate(start=0.92, end=1.38, kind="filler", reason="간투사 '음'", text="음")
    refined = refine_cuts([cut], words)
    assert len(refined) == 1
    assert refined[0].end <= 1.35  # 다음 단어를 침범하지 않음
    assert refined[0].start <= 1.0  # 간투사 자체는 여전히 제거


def test_refine_padding_leaves_breathing_room() -> None:
    words = [_w("음", 1.0, 1.3), _w("다음", 1.6, 2.0)]
    cut = CutCandidate(start=0.9, end=1.58, kind="filler", reason="", text="음")
    refined = refine_cuts([cut], words, pad_before=0.05)
    # 다음 발화(1.6) 50ms 전에서 컷이 끝나야 한다.
    assert abs(refined[0].end - 1.55) < 1e-6


def test_refine_min_cut_drops_short() -> None:
    cuts = [
        CutCandidate(start=1.0, end=1.1, kind="silence", reason=""),  # 0.1초
        CutCandidate(start=2.0, end=3.0, kind="silence", reason=""),  # 1.0초
    ]
    refined = refine_cuts(cuts, [], min_cut=0.2)
    assert len(refined) == 1
    assert refined[0].start == 2.0


def test_refine_passthrough_without_kept_words() -> None:
    cut = CutCandidate(start=1.0, end=2.0, kind="filler", reason="", text="음")
    refined = refine_cuts([cut], [])
    assert refined[0].start == 1.0
    assert refined[0].end == 2.0


def test_refine_does_not_touch_silence_in_gap() -> None:
    # 발화 사이 넉넉한 무음 컷은 단어를 침범하지 않으므로 그대로 유지.
    words = [_w("끝", 0.0, 1.0), _w("시작", 4.0, 5.0)]
    cut = CutCandidate(start=1.2, end=3.8, kind="silence", reason="무음")
    refined = refine_cuts([cut], words)
    assert refined[0].start == 1.2
    assert refined[0].end == 3.8


# ---- FCPXML ----

def test_fcpxml_wellformed_and_clip_count(tmp_path: Path) -> None:
    cuts = [CutCandidate(start=2.0, end=4.0, kind="silence", reason="")]
    out = write_fcpxml(
        cuts, tmp_path / "c.fcpxml", total_duration=10.0, fps=23.976,
        source_path="/media/C0430.mp4",
    )
    root = ET.parse(out).getroot()
    assert root.tag == "fcpxml"
    clips = root.findall(".//asset-clip")
    assert len(clips) == 2  # 남길 구간 [0,2], [4,10]
    fmt = root.find(".//format")
    assert fmt is not None
    # 23.976 → 프레임 정확한 유리수 frameDuration
    assert fmt.get("frameDuration") == "1001/24000s"
    assert "C0430.mp4" in out.read_text(encoding="utf-8")


def test_fcpxml_frame_duration_rates() -> None:
    assert _frame_duration(23.976) == (1001, 24000)
    assert _frame_duration(29.97) == (1001, 30000)
    assert _frame_duration(30.0) == (1, 30)
    assert _frame_duration(25.0) == (1, 25)
    assert _frame_duration(0.0) == (1, 30)


def test_fcpxml_empty_cuts_single_full_clip(tmp_path: Path) -> None:
    out = write_fcpxml([], tmp_path / "full.fcpxml", total_duration=5.0, fps=30.0,
                       source_path="/media/x.mp4")
    root = ET.parse(out).getroot()
    clips = root.findall(".//asset-clip")
    assert len(clips) == 1  # 컷이 없으면 전체가 한 클립
