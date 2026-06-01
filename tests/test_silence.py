"""무음 검출 테스트 (합성 WAV 사용)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from kocut.silence import detect_silences
from kocut.types import Word


def _make_wav(path: Path, segments: list[tuple[float, float, bool]], sr: int = 16000) -> None:
    """segments: (start, end, is_loud) 리스트로 합성 WAV를 만듭니다."""
    total = max(e for _, e, _ in segments)
    samples = np.zeros(int(total * sr), dtype=np.float32)
    rng = np.random.default_rng(42)
    for start, end, is_loud in segments:
        i0 = int(start * sr)
        i1 = int(end * sr)
        if is_loud:
            samples[i0:i1] = rng.normal(0, 0.3, i1 - i0).astype(np.float32)
    sf.write(path, samples, sr)


def test_detects_silence_gap(tmp_path: Path) -> None:
    wav = tmp_path / "test.wav"
    # 0-1s 소리, 1-3s 무음, 3-4s 소리
    _make_wav(wav, [(0.0, 1.0, True), (1.0, 3.0, False), (3.0, 4.0, True)])
    cuts = detect_silences(str(wav), words=None, min_ms=400)
    assert len(cuts) >= 1
    # 검출된 무음이 1~3초 범위 안에 있어야 함
    mid_cut = [c for c in cuts if c.start >= 0.8 and c.end <= 3.2]
    assert len(mid_cut) >= 1


def test_all_cuts_are_silence_kind(tmp_path: Path) -> None:
    wav = tmp_path / "test.wav"
    _make_wav(wav, [(0.0, 0.5, True), (0.5, 2.0, False), (2.0, 2.5, True)])
    cuts = detect_silences(str(wav), min_ms=400)
    for cut in cuts:
        assert cut.kind == "silence"


def test_short_silence_ignored(tmp_path: Path) -> None:
    wav = tmp_path / "test.wav"
    # 0.2초짜리 짧은 무음은 min_ms=400 이면 무시
    _make_wav(wav, [(0.0, 1.0, True), (1.0, 1.2, False), (1.2, 2.0, True)])
    cuts = detect_silences(str(wav), min_ms=400)
    short = [c for c in cuts if 0.9 < c.start < 1.3]
    assert len(short) == 0


def test_speech_protected(tmp_path: Path) -> None:
    wav = tmp_path / "test.wav"
    # 전 구간 무음이지만 word가 있는 곳은 보호
    _make_wav(wav, [(0.0, 4.0, False)])
    words = [Word(word="말", start=1.0, end=2.5, prob=0.9)]
    cuts = detect_silences(str(wav), words=words, min_ms=400)
    # word 구간(1.0~2.5)과 겹치는 컷은 없어야 함
    for cut in cuts:
        assert not (cut.end > 1.0 and cut.start < 2.5)


def test_tiny_audio_returns_empty(tmp_path: Path) -> None:
    # 분석 프레임(2048)보다 짧은 오디오는 빈 결과 (크래시 방지)
    wav = tmp_path / "tiny.wav"
    sf.write(wav, np.random.normal(0, 0.3, 500).astype(np.float32), 16000)
    assert detect_silences(str(wav), min_ms=400) == []


def test_min_ms_zero_no_crash(tmp_path: Path) -> None:
    wav = tmp_path / "mz.wav"
    _make_wav(wav, [(0.0, 1.0, True), (1.0, 2.0, False), (2.0, 3.0, True)])
    result = detect_silences(str(wav), min_ms=0)
    assert isinstance(result, list)
