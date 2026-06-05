"""간투사(filler) 검출.

v0.9부터는 한국어 연결어를 자동으로 자르지 않습니다. 이전 버전은 Kiwi의 MAJ
태그를 그대로 신뢰해 ``근데/그래서/그리고/그런데`` 같은 담화 표지를 간투사로
잘라버렸고, 정상 문장 리듬이 망가졌습니다.

기본 정책:
- ``이제``는 사용자 선호에 맞춰 강제 자동 컷 후보로 보냅니다.
- ``어/음/아/에/흠``처럼 짧은 순수 감탄사만 안전 간투사로 봅니다.
- ``근데/그래서/그리고/그런데`` 등 연결어는 자동 컷도 리뷰 후보도 만들지 않습니다.
"""
from __future__ import annotations

from kocut.types import CutCandidate, CutKind, Word

_FORCE_DELETE_FILLERS = {"이제"}
_CORE_FILLERS = {"어", "음", "아", "에", "흠", "어어", "음음"}
_PROTECTED_DISCOURSE_MARKERS = {
    "근데", "그래서", "그리고", "그런데", "그래도", "그러니까", "그러면", "그럼",
    "또는", "하지만", "다만", "그다음", "그다음에", "다음에", "일단", "자",
    "그", "저", "뭐", "막", "약간", "요거", "이거", "그거", "네",
}
_MAX_CORE_FILLER_DURATION = 0.75
_MAX_FORCE_DELETE_DURATION = 1.35


def _clean_token(text: str) -> str:
    return text.strip().strip('"\'“”‘’').rstrip("?!.,…")


def detect_fillers(words: list[Word], padding: float = 0.06) -> list[CutCandidate]:
    """단어 리스트에서 안전한 간투사 컷 후보를 검출합니다."""
    padding = max(0.0, padding)
    cuts: list[CutCandidate] = []
    for word in words:
        raw = word.word.strip()
        if raw.endswith(("?", "!")):
            continue
        clean = _clean_token(raw)
        if not clean:
            continue
        duration = max(0.0, word.end - word.start)
        if clean in _FORCE_DELETE_FILLERS:
            if duration <= _MAX_FORCE_DELETE_DURATION or duration == 0:
                cuts.append(
                    CutCandidate(
                        start=max(0.0, word.start - padding),
                        end=word.end + padding,
                        kind=CutKind.FILLER,
                        reason="사용자 기본 삭제어 '이제'",
                        text=clean,
                        confidence=0.99,
                    )
                )
            continue
        if clean in _PROTECTED_DISCOURSE_MARKERS:
            continue
        if clean in _CORE_FILLERS and duration <= _MAX_CORE_FILLER_DURATION:
            cuts.append(
                CutCandidate(
                    start=max(0.0, word.start - padding),
                    end=word.end + padding,
                    kind=CutKind.FILLER,
                    reason=f"순수 간투사 '{clean}'",
                    text=clean,
                    confidence=0.92,
                )
            )
    return cuts


FORCE_DELETE_FILLERS = frozenset(_FORCE_DELETE_FILLERS)
PROTECTED_DISCOURSE_MARKERS = frozenset(_PROTECTED_DISCOURSE_MARKERS)
