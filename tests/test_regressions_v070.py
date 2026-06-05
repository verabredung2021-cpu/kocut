"""v0.7.0 회귀 테스트 — 컷백 추격용 품질 엔진."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from kocut import audio, pipeline, quality, transcribe
from kocut.types import CutCandidate, CutKind, Segment, Word
from kocut.audio import MediaInfo


def test_contextual_silence_keeps_short_breaths() -> None:
    words = [
        Word(word="가", start=0.0, end=0.4, prob=0.9),
        Word(word="나", start=0.8, end=1.2, prob=0.9),  # 0.4s gap: 호흡 보존
        Word(word="다", start=3.4, end=3.8, prob=0.9),  # 2.2s gap: 컷
        Word(word="라", start=4.2, end=4.6, prob=0.9),  # 0.4s gap: 보존
        Word(word="마", start=7.2, end=7.6, prob=0.9),  # 2.6s gap: 컷
    ]
    cuts = quality.contextual_silence_cuts(words, 80.0, preset=quality.get_preset("safe"))
    assert len(cuts) == 2
    assert all(c.kind == CutKind.SILENCE for c in cuts)
    assert all(c.duration >= 0.65 for c in cuts)


def test_smooth_cuts_applies_cut_budget() -> None:
    cuts = [
        CutCandidate(start=i * 3.0, end=i * 3.0 + 0.8, kind=CutKind.SILENCE, reason="x")
        for i in range(100)
    ]
    # 10분 영상, 2 cuts/minute면 최대 20개 수준으로 제한되어야 함
    planned = quality.smooth_cuts(
        cuts,
        600.0,
        min_cut_seconds=0.2,
        min_keep_between_cuts_seconds=0.0,
        max_cuts_per_minute=2.0,
        max_remove_ratio=0.5,
    )
    assert len(planned) <= 20


def test_diagnose_cuts_flags_microcuts() -> None:
    cuts = [
        CutCandidate(start=i * 2.0, end=i * 2.0 + 0.2, kind=CutKind.SILENCE, reason="micro")
        for i in range(30)
    ]
    stats = quality.diagnose_cuts(cuts, 120.0)
    assert stats.verdict == "과분할 위험"
    assert stats.cuts_under_500ms == 30


def test_pipeline_writes_variant_edls(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
            text="첫 말 다음 말 마지막 말",
            words=[
                Word(word="첫", start=0.0, end=0.4, prob=0.9),
                Word(word="말", start=0.5, end=0.9, prob=0.9),
                Word(word="다음", start=3.4, end=3.8, prob=0.9),
                Word(word="말", start=4.0, end=4.4, prob=0.9),
                Word(word="마지막", start=8.0, end=8.5, prob=0.9),
                Word(word="말", start=8.7, end=9.2, prob=0.9),
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
        write_variants=True,
        skip_shorts=True,
        skip_fillers=True,
    )

    assert set(result.variant_edl_paths) == set(quality.preset_names())
    assert all(p.exists() for p in result.variant_edl_paths.values())
    assert result.variants_report_path is not None and result.variants_report_path.exists()
