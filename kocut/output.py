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



_EDL_V_EVENT = re.compile(
    r"^\d{3}\s+\S+\s+V\s+C\s+(\d\d:\d\d:\d\d[:;]\d\d)\s+(\d\d:\d\d:\d\d[:;]\d\d)",
    re.M,
)


def parse_edl_keep_ranges(
    edl_text: str, *, fps: float = 30.0, source_start_tc: str | None = None
) -> list[tuple[float, float]]:
    """KoCut EDL에서 '남길 구간'(소스 기준, 초)을 추출합니다.

    소스가 0이 아닌 임베디드 시작 TC로 기록됐다면 ``source_start_tc``를 주면
    그만큼 빼서 파일 선두 0초 기준으로 되돌립니다. ``kocut preview``에서
    원본 파일 기준 trim 범위를 만들 때 사용합니다.
    """
    base = _normalise_fps(fps)
    fps_real = fps if (math.isfinite(fps) and fps > 1.0) else float(base)
    offset = _tc_to_frames(source_start_tc, base) if source_start_tc else 0
    ranges: list[tuple[float, float]] = []
    for si, so in _EDL_V_EVENT.findall(edl_text):
        a = _tc_to_frames(si, base) - offset
        b = _tc_to_frames(so, base) - offset
        if b > a:
            ranges.append((max(0.0, a / fps_real), b / fps_real))
    return ranges

def repair_edl(
    edl_text: str,
    *,
    fps: float = 30.0,
    min_gap_seconds: float = 0.65,
    min_clip_seconds: float = 0.0,
) -> tuple[str, int, int]:
    """기존 EDL의 짧은 삭제 구간을 되살려 인접 클립을 병합합니다 (재분석 불필요).

    원본 영상을 다시 분석하지 않고 EDL의 소스 타임코드만으로 동작합니다.
    ``min_gap_seconds``보다 짧은 '삭제된 구간'(클립 사이 소스 갭)을 되살려 양옆
    클립을 하나로 잇습니다. 과분할(짧은 호흡까지 컷)된 EDL을 즉시 복구하는 용도이며,
    무음 임계값을 바꿔 가며 재실행할 필요 없이 여러 값을 빠르게 시험할 수 있습니다.

    반환: (복구된 EDL 텍스트, 원본 클립 수, 복구 후 클립 수)
    """
    base = _normalise_fps(fps)
    fps_real = fps if (math.isfinite(fps) and fps > 1.0) else float(base)
    min_gap_frames = max(0, int(round(min_gap_seconds * fps_real)))
    min_clip_frames = max(0, int(round(min_clip_seconds * fps_real)))

    events = re.findall(
        r"^\d{3}\s+\S+\s+V\s+C\s+(\d\d:\d\d:\d\d[:;]\d\d)\s+(\d\d:\d\d:\d\d[:;]\d\d)",
        edl_text,
        re.M,
    )
    name_match = re.search(r"\*\s*FROM CLIP NAME:\s*(.+)", edl_text)
    source_name = name_match.group(1).strip() if name_match else "source"

    ranges: list[list[int]] = []
    for si, so in events:
        a, b = _tc_to_frames(si, base), _tc_to_frames(so, base)
        if b > a:
            ranges.append([a, b])
    original_count = len(ranges)

    merged: list[list[int]] = []
    for a, b in ranges:
        if merged and (a - merged[-1][1]) < min_gap_frames:
            merged[-1][1] = max(merged[-1][1], b)  # 짧은 갭 되살려 병합
        else:
            merged.append([a, b])
    if min_clip_frames > 0:
        merged = [r for r in merged if (r[1] - r[0]) >= min_clip_frames]

    lines = ["TITLE: KoCut Edit Repaired", "FCM: NON-DROP FRAME", ""]
    rec = 0
    for i, (a, b) in enumerate(merged, start=1):
        dur = b - a
        si_tc, so_tc = _frames_to_tc(a, base), _frames_to_tc(b, base)
        ri_tc, ro_tc = _frames_to_tc(rec, base), _frames_to_tc(rec + dur, base)
        for track in ("V", "A"):
            lines.append(f"{i:03d}  AX       {track:<5} C        {si_tc} {so_tc} {ri_tc} {ro_tc}")
            lines.append(f"* FROM CLIP NAME: {source_name}")
            lines.append(f"* SOURCE FILE: {source_name}")
        rec += dur
    lines.append("")
    return "\n".join(lines), original_count, len(merged)


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


def write_cut_review_csv(cuts: list[CutCandidate], out_path: Path) -> Path:
    """자동 적용 컷 목록을 CSV로 저장합니다.

    엑셀/구글시트에서 컷 후보를 훑어볼 수 있게 단순 UTF-8-SIG CSV로 씁니다.
    """
    import csv

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "start", "end", "duration", "kind", "confidence", "reason", "text"])
        for i, cut in enumerate(sorted(cuts, key=lambda c: c.start), start=1):
            writer.writerow([
                i,
                f"{cut.start:.3f}",
                f"{cut.end:.3f}",
                f"{cut.duration:.3f}",
                cut.kind,
                f"{cut.confidence:.3f}",
                cut.reason,
                cut.text,
            ])
    return out_path


def write_keep_ranges_csv(
    cuts: list[CutCandidate],
    total_duration: float,
    out_path: Path,
    *,
    min_clip_seconds: float = 0.0,
) -> Path:
    """EDL/FCPXML에 실제로 남는 구간을 CSV로 저장합니다."""
    import csv

    out_path.parent.mkdir(parents=True, exist_ok=True)
    keep_ranges = _invert_cuts(cuts, total_duration, min_keep=max(0.0, min_clip_seconds))
    record_cursor = 0.0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "source_start", "source_end", "source_duration", "record_start", "record_end"])
        for i, (s, e) in enumerate(keep_ranges, start=1):
            dur = e - s
            writer.writerow([
                i,
                f"{s:.3f}",
                f"{e:.3f}",
                f"{dur:.3f}",
                f"{record_cursor:.3f}",
                f"{record_cursor + dur:.3f}",
            ])
            record_cursor += dur
    return out_path
