"""KoCut 데이터 모델.

파이프라인 전체에서 사용하는 타입을 한곳에 정의합니다. 모든 시간 값은
float 초 단위로 통일합니다 (밀리초·ticks 혼용 금지 — 이전 프로젝트에서 단위
혼용이 버그의 큰 원인이었습니다).

start/end 시간 필드는 _TimedModel을 상속해 NaN/inf/음수가 들어오면 자동으로
0으로 보정합니다. 이전 CutBack에서 NaN이 JSON 직렬화를 깨뜨린 문제를 모델
계층에서 원천 차단하기 위함입니다.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator


def _finite(value: float) -> float:
    """NaN/inf는 0.0으로, 음수는 0.0으로 보정합니다."""
    if not math.isfinite(value):
        return 0.0
    return max(0.0, value)


class _TimedModel(BaseModel):
    """start/end 시간 필드를 가진 모델의 공통 베이스."""

    @field_validator("start", "end", check_fields=False)
    @classmethod
    def _ensure_finite_time(cls, v: float) -> float:
        return _finite(v)


class Word(_TimedModel):
    """단어 단위 타임스탬프 (Whisper word-level 출력)."""

    word: str
    start: float
    end: float
    prob: float | None = None


class Segment(_TimedModel):
    """Whisper가 출력하는 발화 구간."""

    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)


class SubtitleSegment(_TimedModel):
    """자막 한 줄 (호흡 단위로 분할된 결과)."""

    index: int
    start: float
    end: float
    text: str


class Utterance(_TimedModel):
    """문장/호흡 단위 발화. v0.8 paper edit와 문장 단위 컷 플래너에서 사용합니다."""

    index: int
    start: float
    end: float
    text: str
    word_count: int = 0
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class TopicSection(_TimedModel):
    """긴 영상 훑기용 토픽/챕터 후보."""

    index: int
    start: float
    end: float
    title: str
    keywords: list[str] = Field(default_factory=list)
    text: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class CutKind:
    """컷 후보 종류 (문자열 상수)."""

    FILLER = "filler"
    SILENCE = "silence"
    RETAKE = "retake"
    LOW_INFO = "low_info"


class CutCandidate(_TimedModel):
    """편집 컷 후보. 모든 검출기가 이 형태로 결과를 냅니다."""

    start: float
    end: float
    kind: str
    reason: str
    text: str = ""
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class ShortsCandidate(_TimedModel):
    """쇼츠(9:16 짧은 클립) 후보 구간."""

    start: float
    end: float
    score: float
    reason: str
    text: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Meta(BaseModel):
    """처리 결과 전체 메타데이터 (JSON 출력용)."""

    source_path: str
    duration: float
    language: str = "ko"
    model: str = ""
    segments: list[Segment] = Field(default_factory=list)
    subtitles: list[SubtitleSegment] = Field(default_factory=list)
    utterances: list[Utterance] = Field(default_factory=list)
    topic_sections: list[TopicSection] = Field(default_factory=list)
    cuts: list[CutCandidate] = Field(default_factory=list)
    filler_candidates: list[CutCandidate] = Field(default_factory=list)
    review_candidates: list[CutCandidate] = Field(default_factory=list)
    shorts: list[ShortsCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("duration")
    @classmethod
    def _ensure_finite_duration(cls, v: float) -> float:
        return _finite(v)
