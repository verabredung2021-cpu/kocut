"""v0.8.0 회귀 테스트 — 문장 단위 director/paper edit 워크플로."""
from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path

from typer.testing import CliRunner

from kocut import audio, director, pipeline, quality, transcribe
from kocut.audio import MediaInfo
from kocut.cli import app
from kocut.types import CutCandidate, CutKind, Meta, Segment, Word


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


def test_build_utterances_groups_sentence_units() -> None:
    words = [
        _w("오늘은", 0.0, 0.4),
        _w("좋습니다", 0.5, 1.1),
        _w("다음", 2.2, 2.6),
        _w("내용입니다", 2.7, 3.4),
    ]
    units = director.build_utterances(words)
    assert len(units) == 2
    assert units[0].text.endswith("좋습니다")
    assert units[1].text.endswith("내용입니다")


def test_sentence_boundary_silence_cuts_ignores_inside_sentence_gap() -> None:
    words = [
        _w("오늘은", 0.0, 0.4),
        _w("수술에", 0.9, 1.3),  # 0.5초 내부 호흡: 같은 문장으로 보호
        _w("대해", 1.4, 1.8),
        _w("말씀드립니다", 1.9, 2.6),
        _w("다음", 5.0, 5.4),  # 문장 사이 2.4초 gap: 컷 후보
        _w("내용입니다", 5.5, 6.2),
    ]
    units = director.build_utterances(words)
    cuts = director.sentence_boundary_silence_cuts(units, 10.0, preset=quality.get_preset("safe"))
    assert len(cuts) == 1
    assert cuts[0].kind == CutKind.SILENCE
    assert cuts[0].start > 2.6
    assert cuts[0].end < 5.0


def test_detect_review_candidates_marks_repetition() -> None:
    words = [
        _w("이", 0.0, 0.2), _w("검사는", 0.2, 0.7), _w("중요합니다", 0.7, 1.4),
        _w("이", 2.0, 2.2), _w("검사는", 2.2, 2.7), _w("중요합니다", 2.7, 3.4),
    ]
    units = director.build_utterances(words)
    candidates = director.detect_review_candidates(units, words)
    assert any(c.kind == CutKind.RETAKE and "반복" in c.reason for c in candidates)


def test_review_decisions_csv_roundtrip(tmp_path: Path) -> None:
    cand = CutCandidate(start=1.0, end=2.0, kind=CutKind.RETAKE, reason="테스트", text="다시")
    csv_path = director.write_review_decisions_csv([cand], tmp_path / "decisions.csv")
    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["decision"] == ""
    assert rows[0]["kind"] == CutKind.RETAKE


def test_apply_decisions_cli_creates_edl(tmp_path: Path) -> None:
    meta = Meta(
        source_path="clip.mp4",
        duration=6.0,
        cuts=[CutCandidate(start=4.0, end=5.0, kind=CutKind.SILENCE, reason="base")],
    )
    meta_path = tmp_path / "clip.meta.json"
    meta_path.write_text(json.dumps(meta.model_dump(), ensure_ascii=False), encoding="utf-8")
    csv_path = tmp_path / "clip.review_decisions.csv"
    csv_path.write_text(
        "decision,start,end,duration,kind,confidence,reason,text\ncut,1.0,2.0,1.0,retake,0.8,review,다시\n",
        encoding="utf-8",
    )
    out = tmp_path / "final.edl"
    runner = CliRunner()
    result = runner.invoke(app, ["apply-decisions", str(meta_path), str(csv_path), "-o", str(out), "--fps", "30"])
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "TITLE: KoCut Edit" in text
    # 자동 컷 + CSV cut 결정 → keep range가 최소 3개로 쪼개짐
    assert "003" in text


def test_pipeline_writes_director_outputs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"\x00")

    def fake_probe(_p: str | Path) -> MediaInfo:
        return MediaInfo(duration=12.0, fps=23.976, width=1920, height=1080, start_tc=None)

    def fake_extract(_v: str | Path, out: str | Path) -> Path:
        Path(out).write_bytes(b"\x00")
        return Path(out)

    def fake_iter(*_args: object, **_kwargs: object) -> Iterator[Segment]:
        yield Segment(
            start=0.0,
            end=10.0,
            text="첫 문장입니다 다음 문장입니다",
            words=[
                _w("첫", 0.0, 0.3), _w("문장입니다", 0.4, 1.2),
                _w("다음", 4.0, 4.3), _w("문장입니다", 4.4, 5.2),
            ],
        )

    monkeypatch.setattr(audio, "probe_media", fake_probe)
    monkeypatch.setattr(audio, "extract_wav", fake_extract)
    monkeypatch.setattr(transcribe, "iter_segments", fake_iter)

    result = pipeline.analyze(
        video,
        tmp_path / "out",
        model="tiny",
        device="cpu",
        compute_type="int8",
        cut_preset="safe",
        write_variants=False,
        skip_shorts=True,
        skip_fillers=True,
    )

    assert result.paper_edit_path.exists()
    assert result.review_candidates_path.exists()
    assert result.html_review_path.exists()
    assert result.meta.utterances
