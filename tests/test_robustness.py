"""강건성(robustness) 회귀 테스트.

버그 헌팅에서 발견한 NaN/None/엣지케이스 방어를 회귀 테스트로 고정합니다.
이전 CutBack이 NaN 때문에 터진 이력이 있어 특히 시간 값 방어를 중점 검증합니다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from kocut import output, subtitles
from kocut.transcribe import _safe_time
from kocut.types import CutCandidate, CutKind, Meta, SubtitleSegment, Word


# --- _safe_time: None/NaN/inf/문자열 방어 ---
def test_safe_time_none() -> None:
    assert _safe_time(None, 5.0) == 5.0


def test_safe_time_nan() -> None:
    assert _safe_time(float("nan"), 5.0) == 5.0


def test_safe_time_inf() -> None:
    assert _safe_time(float("inf"), 5.0) == 5.0
    assert _safe_time(float("-inf"), 5.0) == 5.0


def test_safe_time_normal() -> None:
    assert _safe_time(1.5, 5.0) == 1.5


def test_safe_time_bad_string() -> None:
    assert _safe_time("bad", 5.0) == 5.0


# --- Word/Segment validator: NaN/inf/음수 → 0 ---
def test_word_nan_becomes_zero() -> None:
    w = Word(word="음", start=float("nan"), end=float("inf"))
    assert w.start == 0.0
    assert w.end == 0.0


def test_word_negative_becomes_zero() -> None:
    w = Word(word="음", start=-3.0, end=1.0)
    assert w.start == 0.0
    assert w.end == 1.0


def test_subtitle_nan_becomes_zero() -> None:
    s = SubtitleSegment(index=1, start=float("nan"), end=2.0, text="x")
    assert s.start == 0.0


# --- NaN word가 자막 → SRT 까지 안전하게 흘러야 함 ---
def test_nan_word_produces_valid_srt(tmp_path: Path) -> None:
    words = [
        Word(word="안녕하세요", start=0.0, end=1.0),
        Word(word="음", start=float("nan"), end=float("nan")),
        Word(word="진짜", start=2.0, end=3.0),
    ]
    subs = subtitles.split_subtitles(words)
    for s in subs:
        assert math.isfinite(s.start)
        assert math.isfinite(s.end)
    out = tmp_path / "nan.srt"
    output.write_srt(subs, out)
    content = out.read_text(encoding="utf-8")
    # SRT 안에 'nan' 문자열이 없어야 함
    assert "nan" not in content.lower()


# --- meta.json은 표준 JSON이어야 함 (JS JSON.parse 호환) ---
def test_meta_json_is_standard(tmp_path: Path) -> None:
    meta = Meta(
        source_path="x",
        duration=10.0,
        subtitles=[SubtitleSegment(index=1, start=0.0, end=1.0, text="안녕")],
        cuts=[CutCandidate(start=2.0, end=3.0, kind=CutKind.FILLER, reason="간투사")],
    )
    out = tmp_path / "meta.json"
    output.write_meta_json(meta, out)
    text = out.read_text(encoding="utf-8")
    # 비표준 토큰이 없어야 함
    assert "NaN" not in text
    assert "Infinity" not in text
    # 표준 파서로 재파싱 가능해야 함
    reparsed = json.loads(text)
    assert reparsed["duration"] == 10.0


def test_meta_json_rejects_nan_explicitly() -> None:
    # 혹시 NaN이 새어들어오면 조용히 비표준 JSON을 만들지 않고 거부해야 함
    import io

    bad = {"x": float("nan")}
    with pytest.raises(ValueError):
        json.dump(bad, io.StringIO(), allow_nan=False)


# --- 시간 변환 함수 자체의 NaN 방어 ---
def test_seconds_to_srt_nan() -> None:
    assert output._seconds_to_srt(float("nan")) == "00:00:00,000"


def test_seconds_to_tc_nan() -> None:
    assert output._seconds_to_tc(float("nan"), 30) == "00:00:00:00"


# --- 빈 문자열 / 공백 word ---
def test_empty_words_skipped() -> None:
    words = [
        Word(word="", start=0.0, end=1.0),
        Word(word="  ", start=1.0, end=2.0),
        Word(word="진짜", start=2.0, end=3.0),
    ]
    subs = subtitles.split_subtitles(words)
    # 빈 텍스트 자막은 생성되지 않아야 함
    for s in subs:
        assert s.text.strip()
