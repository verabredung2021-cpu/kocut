"""v0.9.1 회귀 테스트 — v0.6 Premiere XML/preview 부품 이식."""
from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kocut import audio, pipeline, transcribe
from kocut.audio import MediaInfo
from kocut.cli import app
from kocut.output import parse_edl_keep_ranges, write_edl
from kocut.preview import build_filter_script, render_preview
from kocut.quality import smooth_cuts
from kocut.types import CutCandidate, CutKind, Segment, Word
from kocut.xmeml import _pathurl, _rate, write_xmeml


def _sil(s: float, e: float) -> CutCandidate:
    return CutCandidate(start=s, end=e, kind=CutKind.SILENCE, reason="무음", confidence=0.95)


def _w(word: str, start: float, end: float) -> Word:
    return Word(word=word, start=start, end=end, prob=0.9)


# ---- 품질 엔진 가장자리 보호 ----

def test_smooth_protects_short_opening_utterance() -> None:
    cuts = [_sil(0.4, 1.9), _sil(8.0, 8.9)]
    out = smooth_cuts(
        cuts,
        10.0,
        min_cut_seconds=0.5,
        min_keep_between_cuts_seconds=1.0,
        max_cuts_per_minute=None,
        max_remove_ratio=None,
    )
    assert [(c.start, c.end) for c in out] == [(8.0, 8.9)]


def test_smooth_protects_short_closing_utterance() -> None:
    cuts = [_sil(1.0, 2.5), _sil(8.0, 9.6)]
    out = smooth_cuts(
        cuts,
        10.0,
        min_cut_seconds=0.5,
        min_keep_between_cuts_seconds=1.0,
        max_cuts_per_minute=None,
        max_remove_ratio=None,
    )
    assert [(c.start, c.end) for c in out] == [(1.0, 2.5)]


def test_smooth_keeps_edge_silence_when_no_island() -> None:
    cuts = [_sil(0.0, 1.5)]
    out = smooth_cuts(
        cuts,
        10.0,
        min_cut_seconds=0.5,
        min_keep_between_cuts_seconds=1.0,
        max_cuts_per_minute=None,
        max_remove_ratio=None,
    )
    assert [(c.start, c.end) for c in out] == [(0.0, 1.5)]


# ---- Premiere XML (xmeml) ----

def test_xmeml_rate_ntsc() -> None:
    assert _rate(23.976)[:2] == (24, True)
    assert _rate(29.97)[:2] == (30, True)
    assert _rate(25.0)[:2] == (25, False)


def test_xmeml_pathurl_windows() -> None:
    url = _pathurl("D:\\260507_대표원장님\\C0433.mp4")
    assert url.startswith("file://localhost/D:/")
    assert "C0433.mp4" in url
    assert "\\" not in url


def test_xmeml_structure(tmp_path: Path) -> None:
    cuts = [CutCandidate(start=2.0, end=4.0, kind="silence", reason="")]
    out = write_xmeml(
        cuts,
        tmp_path / "c.xml",
        total_duration=10.0,
        fps=23.976,
        source_path="/media/C0433.mp4",
        width=3840,
        height=2160,
    )
    root = ET.parse(out).getroot()
    assert root.tag == "xmeml" and root.get("version") == "4"
    v_clips = root.findall(".//video/track/clipitem")
    a_clips = root.findall(".//audio/track/clipitem")
    assert len(v_clips) == 2 and len(a_clips) == 2
    rate = root.find(".//sequence/rate")
    assert rate is not None
    assert rate.findtext("timebase") == "24" and rate.findtext("ntsc") == "TRUE"
    assert root.find(".//pathurl") is not None
    assert v_clips[1].findtext("in") == "96"


def test_pipeline_writes_premiere_xml(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
            end=6.0,
            text="첫 문장입니다 다음 문장입니다",
            words=[_w("첫", 0.0, 0.3), _w("문장입니다", 0.4, 1.0), _w("다음", 4.0, 4.3), _w("문장입니다", 4.4, 5.1)],
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
    assert result.xmeml_path is not None and result.xmeml_path.exists()
    assert result.xmeml_path.name.endswith(".premiere.xml")


# ---- EDL 파서와 preview ----

def test_parse_edl_keep_ranges_with_offset(tmp_path: Path) -> None:
    out = tmp_path / "t.edl"
    write_edl(
        [CutCandidate(start=2.0, end=4.0, kind="silence", reason="")],
        out,
        total_duration=10.0,
        fps=24.0,
        source_start_tc="01:00:00:00",
    )
    keeps = parse_edl_keep_ranges(out.read_text(encoding="utf-8"), fps=24.0, source_start_tc="01:00:00:00")
    assert [(round(s, 2), round(e, 2)) for s, e in keeps] == [(0.0, 2.0), (4.0, 10.0)]


def test_build_filter_script_structure() -> None:
    script = build_filter_script([(0.0, 0.5), (1.0, 1.5)], height=240)
    assert "trim=start=0.000:end=0.500" in script
    assert "atrim=start=1.000:end=1.500" in script
    assert "scale=-2:240" in script
    assert "concat=n=2:v=1:a=1[outv][outa]" in script


def test_preview_command_is_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["preview", "--help"])
    assert result.exit_code == 0
    assert "EDL" in result.output


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")
def test_render_preview_end_to_end(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=160x120:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
    )
    out = render_preview(src, [(0.0, 0.5), (1.0, 1.5)], tmp_path / "p.mp4", height=120)
    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert abs(float(probe.stdout.strip()) - 1.0) < 0.2
