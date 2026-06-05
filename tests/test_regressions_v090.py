"""v0.9.0 회귀 테스트 — 편집 판단 엔진/이제 제거 정책."""
from __future__ import annotations

import csv
from pathlib import Path

from kocut import director, fillers, quality
from kocut.types import CutCandidate, CutKind, Word


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


def test_korean_connectives_are_not_auto_fillers() -> None:
    words = [
        _w("근데", 0.0, 0.2),
        _w("그래서", 0.3, 0.5),
        _w("그리고", 0.6, 0.8),
        _w("그런데", 0.9, 1.1),
    ]
    assert fillers.detect_fillers(words) == []


def test_ije_is_force_removed_even_in_conservative_mode() -> None:
    cuts = fillers.detect_fillers([_w("이제", 1.0, 1.35)], padding=0.0)
    assert len(cuts) == 1
    assert cuts[0].text == "이제"
    assert cuts[0].confidence >= 0.95
    smoothed = quality.smooth_cuts(
        cuts,
        10.0,
        min_cut_seconds=0.7,
        min_keep_between_cuts_seconds=3.0,
        max_cuts_per_minute=0.1,
        max_remove_ratio=0.001,
    )
    assert len(smoothed) == 1


def test_production_chatter_detected_and_merged() -> None:
    words = [
        _w("입", 0.0, 0.2), _w("지금", 0.2, 0.4), _w("구도", 0.4, 0.6), _w("좋고요", 0.6, 1.0),
        _w("촬영", 1.1, 1.3), _w("준비", 1.3, 1.5), _w("됐습니다", 1.5, 1.8),
        _w("네", 1.9, 2.1), _w("감사합니다", 2.1, 2.5),
        _w("인사부터", 3.0, 3.5), _w("다시", 3.5, 3.8), _w("하세요", 3.8, 4.0),
        _w("오늘은", 7.0, 7.4), _w("시작합니다", 7.4, 8.0),
    ]
    units = director.build_utterances(words, boundary_gap=0.4)
    cuts = director.detect_production_chatter(units)
    assert cuts
    assert cuts[0].kind == CutKind.PRODUCTION
    assert cuts[0].start <= 0.01
    assert cuts[0].end >= 3.9


def test_review_csv_keeps_legacy_columns_and_adds_guidance(tmp_path: Path) -> None:
    cand = CutCandidate(start=1.0, end=2.0, kind=CutKind.PRODUCTION, reason="제작", text="촬영 준비", confidence=0.96)
    path = director.write_review_decisions_csv([cand], tmp_path / "review.csv")
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["decision"] == ""
    assert rows[0]["kind"] == CutKind.PRODUCTION
    assert rows[0]["recommendation"] == "cut"
    assert rows[0]["safety"] == "high"


def test_question_based_topic_sections_are_not_one_giant_block() -> None:
    words = [
        _w("AMH", 0.0, 0.4), _w("수치가", 0.4, 0.8), _w("중요합니다", 0.8, 1.4),
        _w("자궁경", 25.0, 25.5), _w("수술", 25.5, 25.9), _w("이유가", 25.9, 26.3), _w("있나요?", 26.3, 26.8),
        _w("착상", 27.2, 27.6), _w("준비입니다", 27.6, 28.2),
        _w("NK세포", 55.0, 55.5), _w("관련이", 55.5, 55.9), _w("있나요?", 55.9, 56.4),
        _w("면역글로블린", 56.8, 57.5), _w("설명입니다", 57.5, 58.0),
    ]
    sections = director.build_topic_sections(director.build_utterances(words, boundary_gap=0.4), min_duration=10.0)
    assert len(sections) >= 2
    joined = " ".join(",".join(s.keywords) for s in sections)
    assert "자궁경" in joined or "NK세포" in joined
