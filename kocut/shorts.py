"""쇼츠(9:16 짧은 클립) 후보 점수.

트랜스크립트를 슬라이딩 윈도우로 훑으면서, 한국어 훅 키워드와 감정 표현이
많고 간투사가 적은 구간에 높은 점수를 줍니다. 상위 N개를 쇼츠 후보로 추천합니다.
LLM 없이 규칙 기반으로 동작합니다.
"""
from __future__ import annotations

import math
import re

from kocut.types import Segment, ShortsCandidate

# 시청 유지에 강한 훅 키워드
_HOOK_KEYWORDS = (
    "결론", "핵심", "중요", "진짜", "반전", "처음", "마지막", "실패", "성공",
    "후회", "깨달", "위기", "기회", "수익", "돈", "성장", "방법", "비밀",
    "꿀팁", "문제", "해결", "이유", "왜", "어떻게", "절대", "반드시", "사실",
)
# 감정 표현
_EMOTION_KEYWORDS = (
    "웃", "울", "화", "놀", "충격", "소름", "무섭", "재밌", "힘들",
    "행복", "감동", "멘붕", "짜릿", "억울", "대박", "최고",
)
_FILLER_PATTERN = re.compile(r"(^|\s)(어|음|그|뭐|막|약간|이제|저기)(\s|$)")


def _clean(text: str, limit: int = 120) -> str:
    compact = re.sub(r"\s+", " ", text).strip(" .,!?:;\n\t")
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _window_text(segments: list[Segment], start: float, end: float) -> str:
    parts = [s.text for s in segments if s.end > start and s.start < end]
    return " ".join(parts)


def _score_text(text: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    hooks = sum(1 for kw in _HOOK_KEYWORDS if kw in text)
    emotions = sum(1 for kw in _EMOTION_KEYWORDS if kw in text)
    fillers = len(_FILLER_PATTERN.findall(text))

    if hooks:
        score += hooks * 2.0
        reasons.append(f"훅 키워드 {hooks}개")
    if emotions:
        score += emotions * 1.5
        reasons.append(f"감정 표현 {emotions}개")
    # 간투사가 많으면 감점
    score -= fillers * 0.5
    # 적당한 길이의 발화 밀도 보너스
    char_count = len(text)
    if 80 <= char_count <= 600:
        score += 1.0

    return score, reasons


def score_shorts_candidates(
    segments: list[Segment],
    *,
    target_count: int = 5,
    window_s: float = 45.0,
    step_s: float = 15.0,
    min_duration_s: float = 20.0,
    max_duration_s: float = 60.0,
) -> list[ShortsCandidate]:
    """세그먼트 리스트에서 쇼츠 후보 구간을 점수와 함께 추천합니다."""
    if not segments:
        return []

    # 파라미터 가드 — step_s<=0이면 무한루프, window_s<=0이면 의미 없는 결과
    if target_count <= 0:
        return []
    if not math.isfinite(step_s) or step_s <= 0:
        step_s = 15.0
    if not math.isfinite(window_s) or window_s <= 0:
        window_s = 45.0
    if not math.isfinite(min_duration_s) or min_duration_s < 0:
        min_duration_s = 0.0
    if not math.isfinite(max_duration_s) or max_duration_s <= 0:
        max_duration_s = 60.0

    total = max(seg.end for seg in segments)
    candidates: list[ShortsCandidate] = []

    start = 0.0
    while start < total:
        end = min(start + window_s, total)
        if (end - start) >= min_duration_s:
            text = _window_text(segments, start, end)
            if text.strip():
                score, reasons = _score_text(text)
                if score > 0:
                    candidates.append(
                        ShortsCandidate(
                            start=start,
                            end=min(end, start + max_duration_s),
                            score=round(score, 2),
                            reason=", ".join(reasons) if reasons else "발화 밀도",
                            text=_clean(text),
                        )
                    )
        start += step_s

    # 점수 높은 순 정렬 후 시간상 겹치는 후보 제거
    candidates.sort(key=lambda c: c.score, reverse=True)
    selected: list[ShortsCandidate] = []
    for cand in candidates:
        if any(c.end > cand.start and c.start < cand.end for c in selected):
            continue
        selected.append(cand)
        if len(selected) >= target_count:
            break

    selected.sort(key=lambda c: c.start)
    return selected
