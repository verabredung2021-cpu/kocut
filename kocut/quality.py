"""컷 품질/호흡 보정.

KoCut 0.5.0까지의 가장 큰 문제는 RMS 기준 무음 후보를 거의 그대로 EDL에
반영해 말 사이의 0.2~0.5초 호흡까지 잘라 버리는 것이었습니다. 이 모듈은
Cutback류 도구의 핵심 차이를 흉내 냅니다: **볼륨이 낮은 구간**이 아니라
**편집자가 실제로 잘랐을 법한 죽은 공백**만 자동 컷으로 남깁니다.

구현 원칙은 보수적입니다.
- 짧은 무음 컷은 삭제하지 않고 되살립니다.
- 두 컷 사이에 1초 안팎의 짧은 말 조각이 끼면 양옆 무음 컷을 되살려
  마이크로 점프컷을 방지합니다.
- 간투사/재촬영 컷은 원래 의미 기반 컷이므로 무음 품질 필터보다 우선합니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from kocut.types import CutCandidate, CutKind


@dataclass(frozen=True)
class QualityProfile:
    """자동 컷 성향 프리셋.

    시간 단위는 CLI와 맞추기 위해 ms로 저장합니다.
    - min_silence_ms: 오디오/word gap에서 후보로 볼 최소 무음 길이
    - pad_after_ms: 직전 발화 뒤에 남길 호흡
    - pad_before_ms: 다음 발화 앞에 남길 호흡
    - min_silence_cut_ms: 최종적으로 실제 삭제할 최소 무음 길이
    - min_keep_between_cuts_ms: 두 컷 사이에 이보다 짧은 말 조각이 끼면 무음 컷을 되살림
    """

    min_silence_ms: int
    pad_after_ms: int
    pad_before_ms: int
    min_silence_cut_ms: int
    min_keep_between_cuts_ms: int
    min_clip_ms: int
    filler_mode: str


QUALITY_PROFILES: dict[str, QualityProfile] = {
    # 병원/강의/인터뷰 long-form 기본값. 자연스러운 호흡을 남기고 긴 dead air만 제거.
    "longform": QualityProfile(
        min_silence_ms=1000,
        pad_after_ms=280,
        pad_before_ms=180,
        min_silence_cut_ms=700,
        min_keep_between_cuts_ms=1400,
        min_clip_ms=900,
        filler_mode="conservative",
    ),
    # 일반 유튜브 talking-head. longform보다 조금 빠르지만 0.x초 컷 난사는 막음.
    "balanced": QualityProfile(
        min_silence_ms=850,
        pad_after_ms=220,
        pad_before_ms=150,
        min_silence_cut_ms=550,
        min_keep_between_cuts_ms=1000,
        min_clip_ms=700,
        filler_mode="balanced",
    ),
    # 쇼츠/릴스용. 빠르게 붙이되 말 앞뒤는 보호.
    "tight": QualityProfile(
        min_silence_ms=600,
        pad_after_ms=140,
        pad_before_ms=90,
        min_silence_cut_ms=350,
        min_keep_between_cuts_ms=550,
        min_clip_ms=350,
        filler_mode="balanced",
    ),
    # 연구/디버깅용. 과거 버전에 가까운 원시 출력.
    "raw": QualityProfile(
        min_silence_ms=600,
        pad_after_ms=0,
        pad_before_ms=0,
        min_silence_cut_ms=100,
        min_keep_between_cuts_ms=0,
        min_clip_ms=100,
        filler_mode="balanced",
    ),
}


def get_profile(name: str | None) -> QualityProfile:
    """프로필 이름을 안전하게 QualityProfile로 변환합니다."""
    key = (name or "longform").strip().lower()
    if key not in QUALITY_PROFILES:
        allowed = ", ".join(sorted(QUALITY_PROFILES))
        raise ValueError(f"quality profile은 {allowed} 중 하나여야 합니다.")
    return QUALITY_PROFILES[key]


def _merge_overlaps(cuts: list[CutCandidate]) -> list[CutCandidate]:
    """겹치는 같은 종류 컷을 병합합니다. 서로 다른 종류는 앞선 컷 reason을 보존합니다."""
    merged: list[CutCandidate] = []
    for cut in sorted(cuts, key=lambda c: (c.start, c.end)):
        if cut.end <= cut.start:
            continue
        if not merged or cut.start > merged[-1].end:
            merged.append(cut)
            continue
        prev = merged[-1]
        end = max(prev.end, cut.end)
        # 의미 컷(간투사/재촬영)이 섞이면 그 종류를 우선 표시합니다.
        if prev.kind == cut.kind:
            kind = prev.kind
            reason = prev.reason
            text = prev.text or cut.text
            confidence = max(prev.confidence, cut.confidence)
        elif prev.kind != CutKind.SILENCE:
            kind = prev.kind
            reason = prev.reason
            text = prev.text
            confidence = prev.confidence
        elif cut.kind != CutKind.SILENCE:
            kind = cut.kind
            reason = cut.reason
            text = cut.text
            confidence = cut.confidence
        else:
            kind = prev.kind
            reason = prev.reason
            text = prev.text
            confidence = max(prev.confidence, cut.confidence)
        merged[-1] = prev.model_copy(
            update={"end": end, "kind": kind, "reason": reason, "text": text, "confidence": confidence}
        )
    return merged


def smooth_cuts(
    cuts: list[CutCandidate],
    *,
    total_duration: float,
    min_silence_cut_ms: int = 700,
    min_keep_between_cuts_ms: int = 1400,
) -> list[CutCandidate]:
    """최종 EDL 전에 과분할 컷을 줄입니다.

    이 함수는 내용을 삭제하는 방향이 아니라 **짧은 무음을 되살리는 방향**으로만
    동작합니다. 그래서 품질 필터가 틀려도 중요한 발화를 더 많이 보존하는 쪽으로
    실패합니다.
    """
    min_silence_cut = max(0, min_silence_cut_ms) / 1000.0
    min_keep_between = max(0, min_keep_between_cuts_ms) / 1000.0
    total_duration = max(0.0, total_duration)

    # 1) 너무 짧은 무음 삭제는 실제 컷으로 보지 않습니다. 간투사/재촬영은 유지.
    filtered: list[CutCandidate] = []
    for cut in cuts:
        if cut.end <= cut.start:
            continue
        if cut.kind == CutKind.SILENCE and cut.duration < min_silence_cut:
            continue
        filtered.append(cut)

    merged = _merge_overlaps(filtered)
    if not merged or min_keep_between <= 0:
        return merged

    # 2) 두 무음 컷 사이에 짧은 말 조각이 끼면 양옆 무음 컷을 되살립니다.
    #    예: "왔다" [0.25s] "갔다" 같은 짧은 조각 주변을 계속 자르면 1프레임
    #    튀는 점프컷이 생깁니다. 이런 경우는 그냥 이어 두는 편이 훨씬 자연스럽습니다.
    drop: set[int] = set()
    for i in range(len(merged) - 1):
        left = merged[i]
        right = merged[i + 1]
        keep_len = right.start - left.end
        if keep_len <= 0 or keep_len >= min_keep_between:
            continue
        if left.kind == CutKind.SILENCE and right.kind == CutKind.SILENCE:
            drop.add(i)
            drop.add(i + 1)
        elif left.kind == CutKind.SILENCE and right.kind != CutKind.SILENCE:
            drop.add(i)
        elif right.kind == CutKind.SILENCE and left.kind != CutKind.SILENCE:
            drop.add(i + 1)

    if not drop:
        return merged

    smoothed = [c for i, c in enumerate(merged) if i not in drop]
    return _merge_overlaps(smoothed)


def edl_quality_stats(keep_ranges: list[tuple[float, float]], total_duration: float) -> dict[str, float]:
    """EDL/keep range 품질 지표를 계산합니다. 테스트와 리포트용입니다."""
    durations = [max(0.0, e - s) for s, e in keep_ranges if e > s]
    gaps = [max(0.0, b[0] - a[1]) for a, b in zip(keep_ranges, keep_ranges[1:])]
    removed = sum(gaps)
    return {
        "clips": float(len(keep_ranges)),
        "total_duration": max(0.0, total_duration),
        "kept_duration": sum(durations),
        "removed_duration": removed,
        "removed_percent": (removed / total_duration * 100.0) if total_duration > 0 else 0.0,
        "sub_1s_clips": float(sum(d < 1.0 for d in durations)),
        "sub_2s_clips": float(sum(d < 2.0 for d in durations)),
        "sub_500ms_gaps": float(sum(0.0 < g < 0.5 for g in gaps)),
        "sub_800ms_gaps": float(sum(0.0 < g < 0.8 for g in gaps)),
    }
