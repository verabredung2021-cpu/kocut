"""재촬영(retake)/NG 구간 검출.

1-pass 촬영에서 흔한 '다시 갈게요', 'NG' 같은 마커와, 같은 문장을 반복해서
다시 말하는 패턴(인접 발화 유사도)을 찾아 이전 시도(버릴 부분)를 컷 후보로
만듭니다. 유사도는 rapidfuzz로 계산합니다.
"""
from __future__ import annotations

from rapidfuzz import fuzz

from kocut.types import CutCandidate, CutKind, Segment

# NG/재촬영 신호 단어
_NG_MARKERS = ("다시", "잠깐만", "잠깐", "컷", "NG", "엔지", "스톱", "스탑", "끊을게요", "다시 갈게요")
# 인접 세그먼트가 이 이상 유사하면 앞쪽을 재촬영으로 간주
_SIMILARITY_THRESHOLD = 70.0


def _normalize(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace())


def detect_retakes(segments: list[Segment]) -> list[CutCandidate]:
    """세그먼트 리스트에서 재촬영/NG 컷 후보를 검출합니다."""
    cuts: list[CutCandidate] = []

    for seg in segments:
        text = seg.text.strip()
        for marker in _NG_MARKERS:
            if marker in text:
                cuts.append(
                    CutCandidate(
                        start=seg.start,
                        end=seg.end,
                        kind=CutKind.RETAKE,
                        reason=f"NG 마커 '{marker}'",
                        text=text,
                        confidence=0.7,
                    )
                )
                break

    # 인접 세그먼트 유사도 검사 — 앞 세그먼트(이전 시도)를 컷 후보로
    for i in range(len(segments) - 1):
        a = _normalize(segments[i].text)
        b = _normalize(segments[i + 1].text)
        if len(a) < 4 or len(b) < 4:
            continue
        similarity = fuzz.ratio(a, b)
        if similarity >= _SIMILARITY_THRESHOLD:
            cuts.append(
                CutCandidate(
                    start=segments[i].start,
                    end=segments[i].end,
                    kind=CutKind.RETAKE,
                    reason=f"반복 발화 (유사도 {similarity:.0f}%)",
                    text=segments[i].text.strip(),
                    confidence=min(0.9, similarity / 100.0),
                )
            )

    # 시간순 정렬 + 중복 제거 (같은 구간이 마커+유사도로 두 번 잡힐 수 있음)
    cuts.sort(key=lambda c: (c.start, c.end))
    deduped: list[CutCandidate] = []
    for cut in cuts:
        if deduped and abs(cut.start - deduped[-1].start) < 0.05 and abs(cut.end - deduped[-1].end) < 0.05:
            # 더 높은 confidence 유지
            if cut.confidence > deduped[-1].confidence:
                deduped[-1] = cut
            continue
        deduped.append(cut)

    return deduped
