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
import re
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


def _frames_to_tc(total_frames: int, base: int) -> str:
    """프레임 수를 HH:MM:SS:FF(non-drop)로. base는 프레임 자리 롤오버 정수(24/30 등)."""
    total_frames = max(0, total_frames)
    frames = total_frames % base
    total_seconds = total_frames // base
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def _tc_to_frames(tc: str, base: int) -> int:
    """'HH:MM:SS:FF'(또는 ; 구분) 타임코드를 base 기준 프레임 수로. 형식 오류는 0."""
    parts = re.split(r"[:;]", tc.strip())
    if len(parts) != 4:
        return 0
    try:
        h, m, s, f = (int(p) for p in parts)
    except ValueError:
        return 0
    return ((h * 60 + m) * 60 + s) * base + f


def _seconds_to_tc(seconds: float, fps: float = 30.0) -> str:
    """초를 CMX3600 non-drop 타임코드로 변환합니다.

    프레임 개수는 **실제 fps**(예: 23.976)로 세고, 프레임 자리 롤오버만 정수
    베이스(24)로 합니다. 23.976을 24로 반올림해서 프레임까지 세던 이전 방식은
    원본과 타임코드가 시간이 갈수록 어긋났습니다(16분에 ≈24프레임). 실 fps로
    세면 원본 프레임과 정확히 맞습니다. (drop-frame 라벨링은 non-drop 고정.)
    """
    seconds = _safe_duration(seconds)
    base = _normalise_fps(fps)
    fps_real = fps if (math.isfinite(fps) and fps > 1.0) else float(base)
    return _frames_to_tc(int(round(seconds * fps_real)), base)


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


def _invert_cuts(
    cuts: list[CutCandidate], total_duration: float, *, min_keep: float = 0.0
) -> list[tuple[float, float]]:
    """컷(삭제) 구간을 제외한 '남길 구간' 리스트를 만듭니다.

    min_keep보다 짧은 남길 구간(예: 두 컷 사이 0.05초짜리 조각)은 버립니다.
    이런 마이크로 클립은 타임라인에서 사실상 쓸 수 없고 편집을 지저분하게 만들기
    때문에, 양쪽 컷을 그대로 이어 붙이는 편이 결과가 깔끔합니다.
    """
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
    if min_keep > 0:
        keep = [(s, e) for s, e in keep if (e - s) >= min_keep]
    return keep


def write_edl(
    cuts: list[CutCandidate],
    out_path: Path,
    total_duration: float,
    fps: float = 30.0,
    *,
    source_name: str = "source",
    include_audio: bool = True,
    min_clip_seconds: float = 0.0,
    source_start_tc: str | None = None,
) -> Path:
    """컷 후보를 반영한 CMX3600 EDL을 저장합니다 (남길 구간만 이어붙임).

    이전 버전은 트랙 필드에 ``AA/V``를 한 줄로 넣었는데, 일부 NLE에서는 이 값을
    비표준으로 해석해 영상만 열리거나 오디오가 빠질 수 있습니다. 기본값은 각 구간을
    ``V``와 ``A`` 두 줄로 기록해 Premiere/DaVinci가 오디오 트랙도 인식하도록 합니다.

    각 이벤트에는 ``* FROM CLIP NAME``과 ``* SOURCE FILE``을 함께 적습니다. 전자는
    Premiere가, 후자는 DaVinci Resolve가 원본 미디어로 relink할 때 참고합니다.
    ``min_clip_seconds``보다 짧은 남길 구간은 제외해 마이크로 클립을 방지합니다.

    ``source_start_tc``(원본 임베디드 시작 타임코드, 예 ``01:00:00:00``)가 주어지면
    소스 타임코드에 그만큼 오프셋을 더합니다. Sony 등 0이 아닌 시작 TC를 가진
    파일을 TC 기준으로 relink할 때 컷이 어긋나지 않습니다.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keep_ranges = _invert_cuts(cuts, total_duration, min_keep=max(0.0, min_clip_seconds))
    safe_source = (source_name or "source").replace("\n", " ").strip() or "source"
    base = _normalise_fps(fps)
    fps_real = fps if (math.isfinite(fps) and fps > 1.0) else float(base)
    start_frames = _tc_to_frames(source_start_tc, base) if source_start_tc else 0
    lines = ["TITLE: KoCut Edit", "FCM: NON-DROP FRAME", ""]
    record_cursor = 0.0
    tracks = ("V", "A") if include_audio else ("V",)
    for i, (s, e) in enumerate(keep_ranges, start=1):
        src_in = _frames_to_tc(start_frames + int(round(s * fps_real)), base)
        src_out = _frames_to_tc(start_frames + int(round(e * fps_real)), base)
        rec_in = _seconds_to_tc(record_cursor, fps)
        rec_out = _seconds_to_tc(record_cursor + (e - s), fps)
        for track in tracks:
            lines.append(f"{i:03d}  AX       {track:<5} C        {src_in} {src_out} {rec_in} {rec_out}")
            lines.append(f"* FROM CLIP NAME: {safe_source}")
            lines.append(f"* SOURCE FILE: {safe_source}")
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
