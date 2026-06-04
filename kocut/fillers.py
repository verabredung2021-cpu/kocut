"""간투사(filler) 검출.

'어', '음', '그', '저' 같은 군더더기 발화를 단어 단위 타임스탬프 기준으로
찾아 컷 후보로 만듭니다. Kiwi의 감탄사(IC) 태그와 명시적 어휘 목록을 함께
사용하며, '어?'(의문 종결)처럼 간투사가 아닌 경우를 걸러냅니다.
"""
from __future__ import annotations

from kocut.korean import get_analyzer
from kocut.types import CutCandidate, CutKind, Word


# 명시적 간투사 어휘 (Kiwi 태그만으로는 놓치는 것들 보강)
_FILLER_WORDS = {
    "어", "음", "아", "그", "그러니까", "뭐", "막", "약간",
    "저기", "이제", "에", "흠", "어어", "음음", "그그", "뭐랄까",
}
# 거의 항상 군더더기인 핵심 간투사 (높은 confidence). 그 외는 문맥상 의미가
# 있을 수 있어 낮은 confidence로 두고, --filler-mode에서 자동 컷 여부를 정합니다.
_CORE_FILLERS = {"어", "음", "아", "에", "흠", "어어", "음음", "그그"}
# 간투사로 판정할 형태소 태그 (감탄사, 접속부사)
_FILLER_TAGS = {"IC", "MAJ"}
# 간투사 최대 길이 — 이보다 길면 실제 의미 발화일 가능성
_MAX_FILLER_DURATION = 0.7


def detect_fillers(words: list[Word], padding: float = 0.08) -> list[CutCandidate]:
    """단어 리스트에서 간투사 컷 후보를 검출합니다."""
    if padding < 0:
        padding = 0.0
    analyzer = get_analyzer()
    cuts: list[CutCandidate] = []

    for word in words:
        raw = word.word.strip()
        # 의문/감탄 부호로 끝나면 간투사가 아니라 실제 발화 — 제외
        if raw.endswith(("?", "!", ".")):
            continue
        clean = raw.rstrip("?!.,…\"' ")
        if not clean:
            continue

        duration = word.end - word.start
        is_filler = False

        tokens = analyzer.tokenize(clean)
        # 단일 형태소이고 감탄사/접속부사 태그
        if len(tokens) == 1 and tokens[0].tag in _FILLER_TAGS:
            is_filler = True
        # 명시적 간투사 어휘 + 짧은 길이
        if clean in _FILLER_WORDS and duration < _MAX_FILLER_DURATION:
            is_filler = True

        # 너무 길면 간투사 아님 (false positive 방지)
        if duration >= _MAX_FILLER_DURATION:
            is_filler = False

        if is_filler:
            confidence = 0.9 if clean in _CORE_FILLERS else 0.6
            cuts.append(
                CutCandidate(
                    start=max(0.0, word.start - padding),
                    end=word.end + padding,
                    kind=CutKind.FILLER,
                    reason=f"간투사 '{clean}'",
                    text=clean,
                    confidence=confidence,
                )
            )

    return cuts
