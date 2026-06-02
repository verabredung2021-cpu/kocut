"""무음 구간 검출.

librosa로 오디오의 RMS 에너지를 분석해 일정 시간 이상 조용한 구간을 찾습니다.
단어 타임스탬프(words)가 주어지면 발화가 있는 구간은 보호하고 발화 사이의
gap만 검사하므로, 배경 음악이 깔린 구간을 잘못 자르지 않습니다.
"""
from __future__ import annotations

import math

import librosa
import numpy as np

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


def detect_silences(
    wav_path: str,
    words: list[Word] | None = None,
    *,
    min_ms: int = 400,
    padding_ms: int = 120,
    threshold_db: float = -40.0,
) -> list[CutCandidate]:
    """WAV 파일에서 무음 컷 후보를 검출합니다."""
    y, sr = librosa.load(wav_path, sr=16000, mono=True)
    if y.size == 0:
        return []

    hop_length = 512
    frame_length = 2048
    # 분석 프레임보다 짧은 오디오는 무음 판정 불가 — 빈 결과
    if y.size < frame_length:
        return []

    # 파라미터 가드
    if not math.isfinite(min_ms) or min_ms < 0:
        min_ms = 0
    if not math.isfinite(padding_ms) or padding_ms < 0:
        padding_ms = 0
    if not math.isfinite(threshold_db):
        threshold_db = -40.0

    total_duration = y.size / float(sr)
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    max_rms = float(np.max(rms)) if rms.size else 0.0

    # 기존 ref=np.max 방식은 전체가 0인 완전 무음 파일에서 0dB로 정규화되어
    # 무음을 하나도 잡지 못합니다. 최대 RMS가 사실상 0이면 전체를 -100dB로 둡니다.
    if max_rms <= 1e-10:
        rms_db = np.full_like(rms, -100.0, dtype=np.float32)
    else:
        rms_db = librosa.amplitude_to_db(rms, ref=max_rms)

    times = librosa.frames_to_time(np.arange(len(rms_db)), sr=sr, hop_length=hop_length)
    silent_mask = rms_db < threshold_db

    # 연속된 무음 프레임을 구간으로 묶기
    raw_ranges: list[tuple[float, float]] = []
    start_idx: int | None = None
    for i, is_silent in enumerate(silent_mask):
        if is_silent and start_idx is None:
            start_idx = i
        elif not is_silent and start_idx is not None:
            raw_ranges.append((float(times[start_idx]), float(times[i])))
            start_idx = None
    if start_idx is not None:
        # 마지막 무음 구간은 마지막 프레임 시작 시간이 아니라 실제 오디오 끝까지 포함
        raw_ranges.append((float(times[start_idx]), total_duration))

    min_dur = min_ms / 1000.0
    padding = padding_ms / 1000.0

    # 발화 보호 구간 만들기 (word가 있는 시간대는 무음 컷에서 제외)
    speech_spans = sorted((max(0.0, w.start), max(0.0, w.end)) for w in (words or []))

    cuts: list[CutCandidate] = []
    for raw_start, raw_end in raw_ranges:
        if (raw_end - raw_start) < min_dur:
            continue
        for s, e in _subtract_spans(raw_start, raw_end, speech_spans):
            if (e - s) < min_dur:
                continue
            # 안쪽으로 패딩을 줄여 발화 시작/끝을 자르지 않도록
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
