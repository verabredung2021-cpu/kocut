"""컷 후보 정제 (단어 경계 보정 + 패딩 + 최소 길이).

검출기(fillers/silence/retakes)가 만든 컷을 출력 직전에 한 번 더 다듬습니다.
핵심 목적은 **말 앞뒤가 씹히는 문제**를 막는 것입니다. 예를 들어 간투사 컷은
단어 경계에서 양쪽으로 패딩을 더해 확장되는데, 다음 실제 단어와 간격이 좁으면
그 단어의 앞부분을 잘라먹습니다. 이 패스는 모든 컷을 '유지할 단어'의 경계 안쪽으로
끌어와서, 컷이 절대 유지 단어를 침범하지 않도록 보장합니다.

auto-editor / Silenci 류 도구의 교훈을 반영했습니다:
- 무음/threshold만 보고 자르지 말고 단어 타임스탬프로 컷 위치를 보정한다
- 발화 앞뒤에 약간의 여유(padding)를 둔다
- 너무 짧은 컷은 버린다

LLM을 쓰지 않는 결정적(deterministic) 로직입니다.
"""
from __future__ import annotations

from kocut.types import CutCandidate, CutKind, Word


def _kept_word_spans(cuts: list[CutCandidate], words: list[Word]) -> list[tuple[float, float]]:
    """컷으로 '제거되지 않는' 단어들의 (start, end) 구간을 반환합니다.

    단어의 중간점이 어떤 컷 안에 들어가면 그 단어는 제거 대상(간투사·재촬영 등)으로
    보고 보호하지 않습니다. 그 외 단어는 '유지 단어'로 보고 컷이 침범하지 못하게
    합니다.
    """
    spans: list[tuple[float, float]] = []
    for w in words:
        mid = 0.5 * (w.start + w.end)
        removed = any(c.start <= mid <= c.end for c in cuts)
        if not removed and w.end > w.start:
            spans.append((w.start, w.end))
    spans.sort()
    return spans


def refine_cuts(
    cuts: list[CutCandidate],
    words: list[Word],
    *,
    pad_before: float = 0.0,
    pad_after: float = 0.0,
    min_cut: float = 0.0,
) -> list[CutCandidate]:
    """컷을 유지 단어 경계 안쪽으로 보정하고 패딩/최소 길이를 적용합니다.

    - pad_before: 다음 발화가 시작되기 전에 남겨둘 여유(초). 컷의 끝을 그만큼 당깁니다.
    - pad_after:  직전 발화가 끝난 뒤 남겨둘 여유(초). 컷의 시작을 그만큼 미룹니다.
    - min_cut:    이보다 짧아진 컷은 버립니다(자잘한 컷 방지).

    유지 단어가 하나도 없으면(words가 비었거나 전부 제거 대상) 보정 없이
    원본 컷을 그대로 돌려줍니다.
    """
    pad_before = max(0.0, pad_before)
    pad_after = max(0.0, pad_after)
    kept = _kept_word_spans(cuts, words)

    refined: list[CutCandidate] = []
    for cut in sorted(cuts, key=lambda c: (c.start, c.end)):
        s, e = cut.start, cut.end
        if kept:
            center = 0.5 * (s + e)
            # 간투사는 단어 자체가 짧아서 silence용 200ms+ 패딩을 그대로 적용하면
            # 컷이 0초로 쪼그라듭니다. 말 씹힘 방지만 위해 최소 보호 여백을 씁니다.
            local_pad_before = (
                min(pad_before, 0.02) if cut.kind == CutKind.FILLER and pad_before > 0.15 else pad_before
            )
            local_pad_after = (
                min(pad_after, 0.02) if cut.kind == CutKind.FILLER and pad_after > 0.15 else pad_after
            )
            # 컷 중심 기준으로 좌/우에 가장 가까운 유지 단어 경계를 찾습니다.
            prev_ends = [we for (_ws, we) in kept if we <= center]
            next_starts = [ws for (ws, _we) in kept if ws >= center]
            if prev_ends:
                s = max(s, max(prev_ends) + local_pad_after)
            if next_starts:
                e = min(e, min(next_starts) - local_pad_before)
        if e - s <= 0:
            continue
        if min_cut > 0 and (e - s) < min_cut:
            continue
        refined.append(cut.model_copy(update={"start": s, "end": e}))

    return refined
