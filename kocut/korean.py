"""한국어 형태소 분석 유틸리티.

KoCut은 가능하면 Kiwi(kiwipiepy)를 사용합니다. 다만 개발/테스트 환경에서
Kiwi가 설치되지 않았거나 플랫폼 휠 문제가 있을 때 전체 패키지가 import부터
실패하지 않도록, 아주 작은 규칙 기반 fallback을 제공합니다. fallback은 Kiwi만큼
정확하지 않지만 자막 분할과 간투사 검출의 기본 동작은 유지합니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SimpleToken:
    """Kiwi Token에서 KoCut이 사용하는 최소 필드만 맞춘 토큰."""

    form: str
    tag: str


class SimpleKoreanAnalyzer:
    """Kiwi가 없을 때 쓰는 최소 규칙 기반 분석기."""

    _FILLERS = {
        "어", "음", "아", "그", "그러니까", "뭐", "막", "약간",
        "저기", "이제", "에", "흠", "어어", "음음", "그그", "뭐랄까",
    }
    _DEPENDENT_NOUNS = {"수", "것", "거", "줄", "데", "바", "뿐", "때문", "정도", "듯"}
    _PARTICLES = (
        "으로부터", "로부터", "에서", "에게", "께서", "까지", "부터", "보다", "처럼",
        "으로", "라고", "하고", "이며", "이나", "거나", "밖에", "마다", "조차", "마저",
        "은", "는", "이", "가", "을", "를", "에", "의", "도", "만", "로", "과", "와", "랑", "께",
    )
    _FINAL_ENDINGS = (
        "습니다", "습니까", "했어요", "였어요", "이에요", "예요", "어요", "아요", "네요",
        "군요", "죠", "지요", "까요", "입니다", "합니다", "했다", "였다", "한다", "된다",
        "된다", "다", "요", "까", "죠",
    )
    _CONN_ENDINGS = (
        "지만", "는데", "니까", "면서", "려고", "으며", "거나", "고", "서", "면", "니", "며",
    )
    _PUNCT = {".", "?", "!", ",", "…", ":", ";", "~"}

    def tokenize(self, text: str) -> list[SimpleToken]:
        clean = text.strip()
        if not clean:
            return []
        if clean[-1:] in self._PUNCT:
            return [SimpleToken(clean, "SF")]
        if clean in self._FILLERS:
            return [SimpleToken(clean, "IC")]
        if clean in self._DEPENDENT_NOUNS:
            return [SimpleToken(clean, "NNB")]
        if any(clean.endswith(p) and len(clean) > len(p) for p in self._PARTICLES):
            return [SimpleToken(clean, "JX")]
        if any(clean.endswith(e) for e in self._FINAL_ENDINGS):
            return [SimpleToken(clean, "EF")]
        if any(clean.endswith(e) for e in self._CONN_ENDINGS):
            return [SimpleToken(clean, "EC")]
        return [SimpleToken(clean, "NNG")]


_analyzer: Any | None = None
_using_fallback = False


def get_analyzer() -> Any:
    """Kiwi 또는 fallback 분석기를 lazy 생성해 반환합니다."""
    global _analyzer, _using_fallback
    if _analyzer is not None:
        return _analyzer
    try:
        from kiwipiepy import Kiwi  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        _analyzer = SimpleKoreanAnalyzer()
        _using_fallback = True
    else:
        _analyzer = Kiwi()
        _using_fallback = False
    return _analyzer


def using_fallback() -> bool:
    """현재 분석기가 fallback인지 반환합니다."""
    get_analyzer()
    return _using_fallback
