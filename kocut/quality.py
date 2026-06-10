"""컷 품질 안정화 프리셋과 후처리.

KoCut 0.7의 목표는 '더 많이 자르기'가 아니라 '편집자가 납득할 rough cut'입니다.
무음 볼륨 threshold만으로 자르면 짧은 호흡까지 200개 이상 잘리는 문제가 생기므로,
word timestamp가 있으면 단어 사이 gap을 우선 사용하고 컷 예산을 둡니다.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from kocut.types import CutCandidate, CutKind, Word


@dataclass(frozen=True)
class QualityPreset:
    """편집 품질 프리셋."""

    name: str
    min_silence_ms: int
    min_cut_ms: int
    pad_before_ms: int
    pad_after_ms: int
    min_keep_between_cuts_ms: int
    filler_mode: str
    retakes_enabled: bool
    max_cuts_per_minute: float | None = None
    max_remove_ratio: float | None = None


@dataclass(frozen=True)
class CutStats:
    """컷 플랜 품질 진단 수치."""

    clips: int
    cuts: int
    removed_seconds: float
    final_seconds: float
    removal_ratio: float
    median_keep_seconds: float
    median_cut_seconds: float
    cuts_under_500ms: int
    cuts_under_800ms: int
    clips_under_1000ms: int
    clips_under_2000ms: int
    verdict: str


PRESETS: dict[str, QualityPreset] = {
    "_legacy": QualityPreset(
        name="_legacy",
        min_silence_ms=600,
        min_cut_ms=100,
        pad_before_ms=0,
        pad_after_ms=0,
        min_keep_between_cuts_ms=0,
        filler_mode="balanced",
        retakes_enabled=True,
        max_cuts_per_minute=None,
        max_remove_ratio=None,
    ),
    # 병원 상담/강의 기본: 호흡을 보존하고 1.4초 이상 긴 정지만 줄임.
    "safe": QualityPreset(
        name="safe",
        min_silence_ms=1400,
        min_cut_ms=700,
        pad_before_ms=280,
        pad_after_ms=280,
        min_keep_between_cuts_ms=3000,
        filler_mode="conservative",
        retakes_enabled=False,
        max_cuts_per_minute=1.6,
        max_remove_ratio=0.06,
    ),
    "balanced": QualityPreset(
        name="balanced",
        min_silence_ms=1150,
        min_cut_ms=550,
        pad_before_ms=240,
        pad_after_ms=240,
        min_keep_between_cuts_ms=2300,
        filler_mode="conservative",
        retakes_enabled=False,
        max_cuts_per_minute=2.4,
        max_remove_ratio=0.09,
    ),
    # 컷백처럼 빠른 템포를 노리되, 0.3~0.5초 micro cut은 제한.
    "cutback": QualityPreset(
        name="cutback",
        min_silence_ms=950,
        min_cut_ms=430,
        pad_before_ms=190,
        pad_after_ms=210,
        min_keep_between_cuts_ms=1700,
        filler_mode="balanced",
        retakes_enabled=False,
        max_cuts_per_minute=3.5,
        max_remove_ratio=0.13,
    ),
    "aggressive": QualityPreset(
        name="aggressive",
        min_silence_ms=760,
        min_cut_ms=300,
        pad_before_ms=140,
        pad_after_ms=160,
        min_keep_between_cuts_ms=1100,
        filler_mode="balanced",
        retakes_enabled=False,
        max_cuts_per_minute=5.5,
        max_remove_ratio=0.18,
    ),
}

VARIANT_PRESETS: tuple[str, ...] = ("safe", "balanced", "cutback", "aggressive")


def is_forced_user_delete_cut(cut: CutCandidate) -> bool:
    """사용자가 기본 삭제어로 지정한 word-level 컷인지 확인합니다."""
    return cut.kind == CutKind.FILLER and "사용자 기본 삭제어" in (cut.reason or "")


def is_must_keep_cut(cut: CutCandidate) -> bool:
    """컷 예산 때문에 되살리면 안 되는 강제 컷입니다."""
    return is_forced_user_delete_cut(cut) or cut.kind == getattr(CutKind, "PRODUCTION", "production")


def get_preset(name: str | None) -> QualityPreset:
    key = (name or "safe").strip().lower()
    if key not in PRESETS:
        return PRESETS["safe"]
    return PRESETS[key]


def preset_names() -> tuple[str, ...]:
    return tuple(k for k in PRESETS.keys() if not k.startswith("_"))


def preset_pack_names() -> tuple[str, ...]:
    return VARIANT_PRESETS


def _merge_overlaps(cuts: list[CutCandidate]) -> list[CutCandidate]:
    cleaned = [c for c in cuts if c.end > c.start]
    cleaned.sort(key=lambda c: (c.start, c.end))
    merged: list[CutCandidate] = []
    for cut in cleaned:
        if not merged or cut.start > merged[-1].end:
            merged.append(cut)
            continue
        prev = merged[-1]
        merged[-1] = prev.model_copy(
            update={
                "end": max(prev.end, cut.end),
                "confidence": max(prev.confidence, cut.confidence),
                "reason": prev.reason if prev.reason == cut.reason else f"{prev.reason} + {cut.reason}",
            }
        )
    return merged


def _drop_tiny_cuts(cuts: list[CutCandidate], min_cut: float) -> list[CutCandidate]:
    if min_cut <= 0:
        return cuts
    result: list[CutCandidate] = []
    for cut in cuts:
        if is_forced_user_delete_cut(cut):
            floor = 0.03
        elif cut.kind == CutKind.SILENCE:
            floor = min_cut
        elif cut.kind == CutKind.FILLER:
            floor = min(0.20, min_cut)
        elif cut.kind == CutKind.RETAKE:
            floor = min(0.35, min_cut)
        elif cut.kind == getattr(CutKind, "PRODUCTION", "production"):
            floor = min(0.20, min_cut)
        else:
            floor = min_cut
        if cut.duration >= floor:
            result.append(cut)
    return result


def _choose_cut_to_restore(left: CutCandidate, right: CutCandidate) -> CutCandidate:
    if is_must_keep_cut(left) and not is_must_keep_cut(right):
        return right
    if is_must_keep_cut(right) and not is_must_keep_cut(left):
        return left
    priority = {getattr(CutKind, "PRODUCTION", "production"): 5, CutKind.RETAKE: 4, CutKind.SILENCE: 2, CutKind.FILLER: 1}
    lp = priority.get(left.kind, 1)
    rp = priority.get(right.kind, 1)
    if lp != rp:
        return left if lp < rp else right
    if abs(left.duration - right.duration) > 0.05:
        return left if left.duration < right.duration else right
    return left if left.confidence <= right.confidence else right


def _cut_score(cut: CutCandidate) -> float:
    """예산 초과 시 어떤 컷을 우선 살릴지 결정하는 점수."""
    if is_forced_user_delete_cut(cut):
        return 10000.0 + cut.confidence
    kind_bonus = {
        getattr(CutKind, "PRODUCTION", "production"): 120.0,
        CutKind.RETAKE: 100.0,
        CutKind.SILENCE: 20.0,
        CutKind.FILLER: 4.0,
        CutKind.LOW_INFO: 2.0,
    }.get(cut.kind, 1.0)
    return kind_bonus + cut.duration * 10.0 + cut.confidence * 2.0


def _apply_cut_budget(
    cuts: list[CutCandidate],
    total_duration: float,
    *,
    max_cuts_per_minute: float | None,
    max_remove_ratio: float | None,
) -> list[CutCandidate]:
    if not cuts or total_duration <= 0:
        return cuts
    locked = [c for c in cuts if is_must_keep_cut(c)]
    kept = [c for c in cuts if not is_must_keep_cut(c)]
    if max_cuts_per_minute is not None and max_cuts_per_minute > 0:
        max_count = max(1, int(math.ceil((total_duration / 60.0) * max_cuts_per_minute)))
        flexible_budget = max(0, max_count - len(locked))
        if len(kept) > flexible_budget:
            keep_set = set(id(c) for c in sorted(kept, key=_cut_score, reverse=True)[:flexible_budget])
            kept = [c for c in kept if id(c) in keep_set]
    if max_remove_ratio is not None and max_remove_ratio > 0:
        budget = total_duration * max_remove_ratio
        locked_removed = sum(c.duration for c in locked)
        flexible_budget = max(0.0, budget - locked_removed)
        while kept and sum(c.duration for c in kept) > flexible_budget:
            weakest = min(kept, key=_cut_score)
            kept.remove(weakest)
    final = [*locked, *kept]
    final.sort(key=lambda c: c.start)
    return final

def smooth_cuts(
    cuts: list[CutCandidate],
    total_duration: float,
    *,
    min_cut_seconds: float,
    min_keep_between_cuts_seconds: float,
    max_cuts_per_minute: float | None = None,
    max_remove_ratio: float | None = None,
) -> list[CutCandidate]:
    """EDL 과분할을 줄이는 최종 컷 플래너."""
    total_duration = max(0.0, total_duration)
    cuts = [c for c in cuts if c.end > c.start and c.start < total_duration and c.end > 0]
    cuts = [c.model_copy(update={"start": max(0.0, c.start), "end": min(total_duration, c.end)}) for c in cuts]
    cuts = _merge_overlaps(_drop_tiny_cuts(cuts, max(0.0, min_cut_seconds)))

    if min_keep_between_cuts_seconds > 0 and len(cuts) >= 2:
        changed = True
        while changed and len(cuts) >= 2:
            changed = False
            for i in range(len(cuts) - 1):
                keep_len = cuts[i + 1].start - cuts[i].end
                if 0 <= keep_len < min_keep_between_cuts_seconds:
                    if is_must_keep_cut(cuts[i]) and is_must_keep_cut(cuts[i + 1]):
                        continue
                    restore = _choose_cut_to_restore(cuts[i], cuts[i + 1])
                    cuts.pop(i if restore is cuts[i] else i + 1)
                    cuts = _merge_overlaps(cuts)
                    changed = True
                    break

    # 가장자리 보호: 영상 시작/끝의 짧은 실제 발화가 인접 무음 컷 때문에
    # min_clip 출력 필터에서 통째로 사라지는 상황을 막습니다.
    if min_keep_between_cuts_seconds > 0 and cuts:
        edge_drop: set[int] = set()
        first = cuts[0]
        if (
            first.kind == CutKind.SILENCE
            and not is_must_keep_cut(first)
            and 0.0 < first.start < min_keep_between_cuts_seconds
        ):
            edge_drop.add(0)
        last = cuts[-1]
        if (
            last.kind == CutKind.SILENCE
            and not is_must_keep_cut(last)
            and 0.0 < (total_duration - last.end) < min_keep_between_cuts_seconds
        ):
            edge_drop.add(len(cuts) - 1)
        if edge_drop:
            cuts = [c for i, c in enumerate(cuts) if i not in edge_drop]

    cuts = _apply_cut_budget(
        cuts,
        total_duration,
        max_cuts_per_minute=max_cuts_per_minute,
        max_remove_ratio=max_remove_ratio,
    )
    return cuts


def contextual_silence_cuts(
    words: list[Word],
    total_duration: float,
    *,
    preset: QualityPreset,
    min_silence_ms: int | None = None,
    pad_before_ms: int | None = None,
    pad_after_ms: int | None = None,
    min_cut_ms: int | None = None,
) -> list[CutCandidate]:
    """단어 gap 기반 무음 컷 생성.

    Cutback류 도구의 핵심은 fixed threshold가 아니라 말끝/호흡/문맥을 고려하는 것입니다.
    여기서는 완전한 AI 모델 대신 Whisper word timestamp를 사용해 발화 사이 gap만 대상으로
    삼고, 문장 경계와 gap 길이로 confidence를 부여합니다.
    """
    valid = sorted((w for w in words if w.end > w.start), key=lambda w: (w.start, w.end))
    if len(valid) < 2:
        return []

    min_gap = max(0.0, (preset.min_silence_ms if min_silence_ms is None else min_silence_ms) / 1000.0)
    pad_before = max(0.0, (preset.pad_before_ms if pad_before_ms is None else pad_before_ms) / 1000.0)
    pad_after = max(0.0, (preset.pad_after_ms if pad_after_ms is None else pad_after_ms) / 1000.0)
    min_cut = max(0.0, (preset.min_cut_ms if min_cut_ms is None else min_cut_ms) / 1000.0)

    pairs: list[tuple[Word, Word, float]] = []
    samples: list[float] = []
    for prev, nxt in zip(valid, valid[1:]):
        gap = nxt.start - prev.end
        if gap <= 0:
            continue
        pairs.append((prev, nxt, gap))
        if 0.12 <= gap <= 12.0:
            samples.append(gap)

    # 영상마다 말 빠르기가 다르므로 gap 분포를 보고 기준을 올립니다.
    # 느린 강의/상담 영상에서는 0.8초 쉼도 자연스러운 호흡일 수 있습니다.
    q_by_profile = {"safe": 0.88, "balanced": 0.80, "cutback": 0.70, "aggressive": 0.58}
    q = q_by_profile.get(preset.name, 0.0)
    if q and len(samples) >= 8:
        ordered = sorted(samples)
        pos = q * (len(ordered) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        frac = pos - lo
        adaptive_gap = ordered[lo] * (1.0 - frac) + ordered[hi] * frac
        threshold = max(min_gap, adaptive_gap)
    else:
        threshold = min_gap

    candidates: list[CutCandidate] = []
    for prev, nxt, gap in pairs:
        if gap < threshold:
            continue
        # 짧은 gap일수록 여유를 더 남겨 숨과 말끝을 보존합니다.
        extra_breath = max(0.0, min(0.16, (1.60 - gap) * 0.10))
        start = prev.end + pad_after + extra_breath
        end = nxt.start - pad_before - extra_breath
        dur = end - start
        if dur < min_cut:
            continue
        prev_txt = prev.word.strip()
        nxt_txt = nxt.word.strip()
        boundary_bonus = 0.06 if prev_txt.endswith((".", "?", "!", "다", "요", "죠", "네")) else 0.0
        long_bonus = min(0.18, max(0.0, gap - threshold) / 6.0)
        confidence = min(0.98, 0.76 + long_bonus + boundary_bonus)
        candidates.append(
            CutCandidate(
                start=start,
                end=end,
                kind=CutKind.SILENCE,
                reason=f"문맥 무음 gap {gap:.2f}초 → {dur:.2f}초 삭제 · {preset.name}",
                text=f"{prev_txt} … {nxt_txt}".strip(),
                confidence=confidence,
            )
        )
    return candidates


def invert_cuts(cuts: list[CutCandidate], total_duration: float) -> list[tuple[float, float]]:
    if not math.isfinite(total_duration) or total_duration <= 0:
        return []
    merged: list[list[float]] = []
    for cut in sorted(cuts, key=lambda c: (c.start, c.end)):
        s, e = max(0.0, cut.start), min(total_duration, cut.end)
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


def diagnose_cuts(cuts: list[CutCandidate], total_duration: float) -> CutStats:
    duration = max(0.0, total_duration if math.isfinite(total_duration) else 0.0)
    keep = invert_cuts(cuts, duration)
    cut_durs = [c.duration for c in cuts if c.duration > 0]
    keep_durs = [e - s for s, e in keep if e > s]
    removed = sum(cut_durs)
    final = max(0.0, duration - removed)
    ratio = (removed / duration) if duration > 0 else 0.0
    med_keep = statistics.median(keep_durs) if keep_durs else 0.0
    med_cut = statistics.median(cut_durs) if cut_durs else 0.0
    clips_under_1 = sum(d < 1.0 for d in keep_durs)
    clips_under_2 = sum(d < 2.0 for d in keep_durs)
    cuts_under_05 = sum(d < 0.5 for d in cut_durs)
    cuts_under_08 = sum(d < 0.8 for d in cut_durs)

    if len(keep) > max(80, duration / 8) or cuts_under_05 > 5 or (cut_durs and med_cut < 0.50):
        verdict = "과분할 위험"
    elif ratio > 0.18 or clips_under_2 > max(5, len(keep) // 5):
        verdict = "검토 필요"
    else:
        verdict = "안전"

    return CutStats(
        clips=len(keep),
        cuts=len(cut_durs),
        removed_seconds=removed,
        final_seconds=final,
        removal_ratio=ratio,
        median_keep_seconds=med_keep,
        median_cut_seconds=med_cut,
        cuts_under_500ms=cuts_under_05,
        cuts_under_800ms=cuts_under_08,
        clips_under_1000ms=clips_under_1,
        clips_under_2000ms=clips_under_2,
        verdict=verdict,
    )
