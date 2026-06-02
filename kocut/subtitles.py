"""한국어 자막 분할.

Whisper의 단어 단위 타임스탬프를 받아 사람이 읽기 좋은 '호흡 단위'로 끊습니다.
Kiwi 형태소 분석기로 각 어절의 마지막 형태소 품사를 확인해서, 종결어미(EF)나
연결어미(EC) 뒤에서 자연스럽게 끊고, 조사(J*)·의존명사(NNB) 뒤에서는 끊지
않습니다 (예: "보호할 수 / 있을까요" 같은 어색한 분할 방지).

이 모듈은 LLM을 쓰지 않습니다. 순수 규칙 기반이라 비용이 0이고 결정적입니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from kocut.korean import get_analyzer
from kocut.types import SubtitleSegment, Word


@dataclass(frozen=True)
class SplitOptions:
    # 강의·인터뷰에 맞춰 넉넉히 묶되, 과하게 길어지지 않도록 균형
    # (실제 50분 인터뷰 SRT로 튜닝한 값)
    max_words: int = 12
    max_duration: float = 4.0
    min_duration: float = 0.8
    max_chars: int = 38


# 끊기 적합/부적합 형태소 태그
_FINAL_ENDING = "EF"  # 종결어미 (-다, -요, -까, -습니다)
_CONN_ENDING = "EC"  # 연결어미 (-고, -서, -면, -지만)
_PUNCT_TAGS = {"SF", "SP", "SS", "SE", "SO"}  # 구두점
_PARTICLE_PREFIX = "J"  # 조사 (JKS, JKO, JX, JKB, ...)
_DEPENDENT_NOUN = "NNB"  # 의존명사 (수, 것, 거, 줄, 데, 바, 때문 ...)


def _last_tag(word_text: str) -> str:
    """어절의 마지막 형태소 품사 태그를 반환합니다."""
    analyzer = get_analyzer()
    tokens = analyzer.tokenize(word_text)
    if not tokens:
        return ""
    return str(tokens[-1].tag)


def _score_from_tag(last_tag: str, next_gap: float) -> float:
    """마지막 품사와 다음 단어 간격으로 '끊기 적합도'를 점수화합니다."""
    score = 0.0
    if last_tag == _FINAL_ENDING:
        score += 10.0
    elif last_tag in _PUNCT_TAGS:
        score += 8.0
    elif last_tag == _CONN_ENDING:
        score += 5.0
    elif last_tag == _DEPENDENT_NOUN:
        score -= 8.0  # 의존명사 뒤는 끊으면 안 됨
    elif last_tag.startswith(_PARTICLE_PREFIX):
        score -= 5.0
    # 다음 단어와의 간격이 크면 자연스러운 쉼 → 끊기 좋은 자리
    if next_gap > 0.5:
        score += 4.0
    elif next_gap > 0.2:
        score += 2.0
    return score


def split_subtitles(words: list[Word], options: SplitOptions | None = None) -> list[SubtitleSegment]:
    """단어 리스트를 자막 세그먼트 리스트로 분할합니다."""
    options = options or SplitOptions()
    if not words:
        return []

    subtitles: list[SubtitleSegment] = []
    index = 1
    current: list[Word] = []

    def flush() -> None:
        nonlocal index
        if not current:
            return
        start = current[0].start
        end = current[-1].end
        text = " ".join(w.word.strip() for w in current if w.word.strip()).strip()
        if text:
            subtitles.append(SubtitleSegment(index=index, start=start, end=end, text=text))
            index += 1

    for i, word in enumerate(words):
        current.append(word)
        duration = current[-1].end - current[0].start
        char_count = sum(len(w.word.strip()) for w in current)
        next_gap = (words[i + 1].start - word.end) if i + 1 < len(words) else 999.0

        last_tag = _last_tag(word.word)
        score = _score_from_tag(last_tag, next_gap)
        avoid_break = last_tag == _DEPENDENT_NOUN or last_tag.startswith(_PARTICLE_PREFIX)
        # 다음 어절이 의존명사로 시작하면 ("수", "것" 등) 현재 단어와 묶여야 함
        if not avoid_break and i + 1 < len(words):
            next_tokens = get_analyzer().tokenize(words[i + 1].word)
            if next_tokens and next_tokens[0].tag == _DEPENDENT_NOUN:
                avoid_break = True

        # 강제 분할: 너무 길어지면 무조건 끊음
        force = (
            len(current) >= options.max_words
            or duration >= options.max_duration
            or char_count >= options.max_chars
        )
        # 단, 의존명사/조사로 끝나면 어색하니 안전 한계 내에서 다음 어절까지 미룸
        if (
            force
            and avoid_break
            and i + 1 < len(words)
            and len(current) < options.max_words + 4
            and char_count < options.max_chars + 15
            and duration < options.max_duration + 2.0
        ):
            force = False

        # 자연 분할: 종결어미/구두점 + 최소 길이 충족
        natural = score >= 8.0 and duration >= options.min_duration

        if force or natural:
            flush()
            current = []

    flush()
    return subtitles
