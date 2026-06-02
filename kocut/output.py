"""결과 파일 출력.

세 가지 형식을 생성합니다:
- SRT: 한국어 자막 (Premiere / DaVinci / YouTube 에 import)
- EDL (CMX3600): 컷 후보를 제외하고 '남길 구간'을 이어 붙인 편집 결정 리스트
- JSON: 자체 GUI / 디버깅용 전체 메타데이터

Premiere/DaVinci 가 SDK 없이 그대로 읽을 수 있는 표준 텍스트 포맷만 씁니다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from kocut.types import CutCandidate, Meta, SubtitleSegment


def _normalise_fps(fps: float) -> int:
    """EDL timecode 계산에 사용할 안전한 정수 FPS를 반환합니다."""
    if not math.isfinite(fps) or fps <= 0:
        return 30
    fps_i = int(round(fps))
    return fps_i if fps_i > 0 else 30


def _safe_duration(seconds: float) -> float:
    """NaN/inf/음수를 0초로 보정합니다."""
    if not math.isfinite(seconds):
        return 0.0
    return max(0.0, seconds)


def _seconds_to_srt(seconds: float) -> str:
    seconds = _safe_duration(seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:  # 반올림이 올림으로 넘어간 경우 보정
        seconds += 1
        ms = 0
    total = int(seconds)
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _seconds_to_tc(seconds: float, fps: float = 30.0) -> str:
    seconds = _safe_duration(seconds)
    fps_i = _normalise_fps(fps)
    total_frames = int(round(seconds * fps_i))
    frames = total_frames % fps_i
    total_seconds = total_frames // fps_i
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def write_srt(subtitles: list[SubtitleSegment], out_path: Path) -> Path:
    """자막을 SubRip(.srt) 형식으로 저장합니다."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for sub in subtitles:
        blocks.append(str(sub.index))
        blocks.append(f"{_seconds_to_srt(sub.start)} --> {_seconds_to_srt(sub.end)}")
        blocks.append(sub.text.strip())
        blocks.append("")
    out_path.write_text("\n".join(blocks), encoding="utf-8")
    return out_path


def _invert_cuts(cuts: list[CutCandidate], total_duration: float) -> list[tuple[float, float]]:
    """컷(삭제) 구간을 제외한 '남길 구간' 리스트를 만듭니다."""
    total_duration = _safe_duration(total_duration)
    if total_duration <= 0:
        return []
    # 겹치는 컷을 병합
    sorted_cuts = sorted(((c.start, c.end) for c in cuts), key=lambda x: x[0])
    merged: list[list[float]] = []
    for s, e in sorted_cuts:
        s = max(0.0, s)
        e = min(total_duration, e)
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in merged:
        if s > cursor:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < total_duration:
        keep.append((cursor, total_duration))
    return keep


def write_edl(cuts: list[CutCandidate], out_path: Path, total_duration: float, fps: float = 30.0) -> Path:
    """컷 후보를 반영한 CMX3600 EDL을 저장합니다 (남길 구간만 이어붙임)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keep_ranges = _invert_cuts(cuts, total_duration)
    lines = ["TITLE: KoCut Edit", "FCM: NON-DROP FRAME", ""]
    record_cursor = 0.0
    for i, (s, e) in enumerate(keep_ranges, start=1):
        src_in = _seconds_to_tc(s, fps)
        src_out = _seconds_to_tc(e, fps)
        rec_in = _seconds_to_tc(record_cursor, fps)
        rec_out = _seconds_to_tc(record_cursor + (e - s), fps)
        lines.append(f"{i:03d}  AX       AA/V  C        {src_in} {src_out} {rec_in} {rec_out}")
        lines.append(f"* FROM CLIP NAME: source")
        record_cursor += e - s
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_meta_json(meta: Meta, out_path: Path) -> Path:
    """전체 메타데이터를 pretty JSON으로 저장합니다.

    allow_nan=False로 비표준 NaN/Infinity 토큰을 막아, JavaScript 등
    표준 JSON 파서가 결과를 읽을 수 있도록 보장합니다. (모델 계층에서 이미
    시간 값을 보정하므로 정상 흐름에서는 NaN이 없습니다.)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(meta.model_dump(), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return out_path
