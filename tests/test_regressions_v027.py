"""v0.2.7 회귀 테스트."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

from kocut.output import _invert_cuts, _seconds_to_tc, write_srt
from kocut.shorts import score_shorts_candidates
from kocut.silence import detect_silences
from kocut.types import Segment, SubtitleSegment, Word


def test_all_zero_audio_detected_as_silence(tmp_path: Path) -> None:
    wav = tmp_path / "zero.wav"
    sf.write(wav, np.zeros(16000 * 3, dtype=np.float32), 16000)
    cuts = detect_silences(str(wav), min_ms=400)
    assert cuts
    assert cuts[0].kind == "silence"


def test_silence_split_around_speech_span(tmp_path: Path) -> None:
    wav = tmp_path / "zero_with_word.wav"
    sf.write(wav, np.zeros(16000 * 4, dtype=np.float32), 16000)
    words = [Word(word="말", start=1.0, end=2.5)]
    cuts = detect_silences(str(wav), words=words, min_ms=400, padding_ms=0)
    assert any(c.end <= 1.0 for c in cuts)
    assert any(c.start >= 2.5 for c in cuts)
    assert all(not (c.end > 1.0 and c.start < 2.5) for c in cuts)


def test_tiny_positive_fps_does_not_crash() -> None:
    assert _seconds_to_tc(1.0, 0.1) == "00:00:01:00"


def test_nan_total_duration_returns_empty_keep_ranges() -> None:
    assert _invert_cuts([], total_duration=math.nan) == []


def test_write_srt_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "a" / "test.srt"
    write_srt([SubtitleSegment(index=1, start=0.0, end=1.0, text="안녕")], out)
    assert out.exists()


def test_shorts_target_count_zero_returns_empty() -> None:
    seg = Segment(start=0.0, end=30.0, text="진짜 핵심 결론", words=[])
    assert score_shorts_candidates([seg], target_count=0) == []
