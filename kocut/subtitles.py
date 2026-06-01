"""한국어 자막 분할.

Whisper의 단어 단위 타임스탬프를 받아 사람이 읽기 좋은 '호흡 단위'로 끊습니다.
Kiwi 형태소 분석기로 각 어절의 마지막 형태소 품사를 확인해서, 종결어미(EF)나
연결어미(EC) 뒤에서 자연스럽게 끊고, 조사(J*) 뒤에서는 끊지 않습니다.

이 모듈은 LLM을 쓰지 않습니다. 순수 규칙 기반이라 비용이 0이고 결정적입니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from kiwipiepy import Kiwi

from kocut.types import SubtitleSegment, Word

_kiwi: Kiwi | None = None


def _get_kiwi() -> Kiwi:
    """Kiwi 인스턴스를 lazy 생성 (import 시점에 로딩하지 않음)."""
    global _kiwi
    if _kiwi is None:
        _kiwi = Kiwi()
    return _kiwi


@dataclass(frozen=True)
class SplitOptions:
    max_words: int = 8
    max_duration: float = 2.5
    min_duration: float = 0.6
    max_chars: int = 40


# 끊기 적합/부적합 형태소 태그
_FINAL_ENDING = "EF"  # 종결어미 (-다, -요, -까, -습니다)
_CONN_ENDING = "EC"  # 연결어미 (-고, -서, -면, -지만)
_PUNCT_TAGS = {"SF", "SP", "SS", "SE", "SO"}  # 구두점
_PARTICLE_PREFIX = "J"  # 조사 (JKS, JKO, JX, JKB, ...)


def _break_score(word_text: str, next_gap: float) -> float:
    """이 단어 뒤에서 자막을 끊는 게 얼마나 적합한지 점수화합니다."""
    kiwi = _get_kiwi()
    tokens = kiwi.tokenize(word_text)
    if not tokens:
        return 0.0
    last = tokens[-1]
    score = 0.0
    if last.tag == _FINAL_ENDING:
        score += 10.0
    elif last.tag in _PUNCT_TAGS:
        score += 8.0
    elif last.tag == _CONN_ENDING:
        score += 5.0
    elif last.tag.startswith(_PARTICLE_PREFIX):
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

        score = _break_score(word.word, next_gap)

        # 강제 분할: 너무 길어지면 무조건 끊음
        force = (
            len(current) >= options.max_words
            or duration >= options.max_duration
            or char_count >= options.max_chars
        )
        # 자연 분할: 종결어미/구두점 + 최소 길이 충족
        natural = score >= 8.0 and duration >= options.min_duration

        if force or natural:
            flush()
            current = []

    flush()
    return subtitles
