"""무음 구간 검출.

v0.5.1부터 librosa/numba 대신 soundfile+numpy만 사용합니다. 이전 구현은
Python 3.13/numba 조합에서 테스트가 멈추거나 크래시나는 경우가 있었고, 실제
무음 검출에는 STFT가 필요하지 않았습니다. 여기서는 단순 RMS 에너지로 후보를
잡고, 단어 타임스탬프가 있으면 발화 구간을 보호합니다.
"""
from __future__ import annotations

import math

import numpy as np
import soundfile as sf

from kocut.types import CutCandidate, CutKind, Word


def _subtract_spans(start: float, end: float, protected: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[start, end]에서 protected 구간을 빼고 남는 구간을 반환합니다."""
    if end <= start:
        return []
    ranges = [(start, end)]
    for ps, pe in protected:
        if pe <= ps:
            continue
        next_ranges: list[tuple[float, float]] = []
        for rs, re in ranges:
            if pe <= rs or ps >= re:
                next_ranges.append((rs, re))
                continue
            if ps > rs:
                next_ranges.append((rs, min(ps, re)))
            if pe < re:
                next_ranges.append((max(pe, rs), re))
        ranges = next_ranges
        if not ranges:
            break
    return ranges


def _load_mono(path: str) -> tuple[np.ndarray, int]:
    """오디오를 mono float32로 읽습니다. KoCut extract_wav 출력은 이미 16k mono입니다."""
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr.mean(axis=1, dtype=np.float32)
    if arr.ndim != 1:
        arr = arr.reshape(-1).astype(np.float32)
    return arr, int(sr)


def _rms_frames(y: np.ndarray, *, sr: int, frame_length: int, hop_length: int) -> tuple[np.ndarray, np.ndarray]:
    """프레임 시작 시간과 RMS 값을 반환합니다. 누적합으로 빠르게 계산합니다."""
    if y.size < frame_length:
        return np.array([], dtype=np.float32), np.array([], dtype=np.float32)
    starts = np.arange(0, y.size - frame_length + 1, hop_length, dtype=np.int64)
    sq = np.square(y, dtype=np.float64)
    cumsum = np.concatenate(([0.0], np.cumsum(sq)))
    sums = cumsum[starts + frame_length] - cumsum[starts]
    rms = np.sqrt(np.maximum(sums / frame_length, 0.0)).astype(np.float32)
    times = starts.astype(np.float32) / float(sr)
    return times, rms


def detect_silences(
    wav_path: str,
    words: list[Word] | None = None,
    *,
    min_ms: int = 400,
    padding_ms: int = 120,
    threshold_db: float = -40.0,
) -> list[CutCandidate]:
    """WAV 파일에서 무음 컷 후보를 검출합니다.

    `threshold_db`는 파일 내 최대 RMS 대비 dB입니다. 예: -40이면 최대 발화 RMS보다
    40dB 이상 낮은 구간을 무음 후보로 봅니다. word timestamp가 들어오면 해당
    발화 구간은 무조건 보호합니다.
    """
    try:
        y, sr = _load_mono(wav_path)
    except Exception:  # pragma: no cover - 손상 파일은 상위에서 처리되는 편이 일반적
        return []
    if y.size == 0 or sr <= 0:
        return []

    # 기존 librosa 설정(16k 기준 frame=2048/hop=512)의 시간 해상도를 유지합니다.
    frame_length = max(16, int(round(sr * (2048 / 16000))))
    hop_length = max(1, int(round(sr * (512 / 16000))))
    if y.size < frame_length:
        return []

    if not math.isfinite(min_ms) or min_ms < 0:
        min_ms = 0
    if not math.isfinite(padding_ms) or padding_ms < 0:
        padding_ms = 0
    if not math.isfinite(threshold_db):
        threshold_db = -40.0

    total_duration = y.size / float(sr)
    times, rms = _rms_frames(y, sr=sr, frame_length=frame_length, hop_length=hop_length)
    if rms.size == 0:
        return []
    max_rms = float(np.max(rms))
    if max_rms <= 1e-10:
        rms_db = np.full_like(rms, -100.0, dtype=np.float32)
    else:
        rms_db = 20.0 * np.log10(np.maximum(rms, 1e-12) / max_rms)

    silent_mask = rms_db < threshold_db

    raw_ranges: list[tuple[float, float]] = []
    start_idx: int | None = None
    for i, is_silent in enumerate(silent_mask):
        if bool(is_silent) and start_idx is None:
            start_idx = i
        elif not bool(is_silent) and start_idx is not None:
            # 프레임 시작~프레임 끝 기준. 이전 librosa 구현보다 끝점을 조금 더 보수적으로 잡습니다.
            raw_ranges.append((float(times[start_idx]), min(total_duration, float(times[i]) + frame_length / sr)))
            start_idx = None
    if start_idx is not None:
        raw_ranges.append((float(times[start_idx]), total_duration))

    min_dur = min_ms / 1000.0
    padding = padding_ms / 1000.0
    speech_spans = sorted((max(0.0, w.start), max(0.0, w.end)) for w in (words or []))

    cuts: list[CutCandidate] = []
    for raw_start, raw_end in raw_ranges:
        if (raw_end - raw_start) < min_dur:
            continue
        for s, e in _subtract_spans(raw_start, raw_end, speech_spans):
            if (e - s) < min_dur:
                continue
            cut_start = s + padding
            cut_end = e - padding
            if cut_end <= cut_start:
                continue
            if (cut_end - cut_start) < min_dur:
                continue
            cuts.append(
                CutCandidate(
                    start=cut_start,
                    end=cut_end,
                    kind=CutKind.SILENCE,
                    reason=f"무음 {cut_end - cut_start:.1f}초",
                    text="",
                    confidence=0.95,
                )
            )

    return cuts
