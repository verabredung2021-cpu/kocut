"""v0.4.0 회귀 테스트.

ffprobe 파서, EDL 시작 TC 오프셋, 간투사 모드 분리, 미리보기 리포트, 그리고
공용 파이프라인(analyze) 통합 테스트를 검증합니다.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from kocut import audio, pipeline, silence, transcribe
from kocut.audio import MediaInfo, _parse_probe
from kocut.output import _frames_to_tc, _tc_to_frames, write_edl
from kocut.pipeline import _split_fillers
from kocut.review import write_review
from kocut.types import CutCandidate, Meta, Segment, SubtitleSegment, Word


# ---- ffprobe 파서 ----

def test_parse_probe_reads_fps_resolution_tc() -> None:
    info = _parse_probe(
        [{
            "codec_type": "video", "r_frame_rate": "24000/1001",
            "width": 3840, "height": 2160, "tags": {"timecode": "01:00:00:00"},
        }],
        {"duration": "123.4"},
    )
    assert abs(info.fps - 24000 / 1001) < 1e-6
    assert (info.width, info.height) == (3840, 2160)
    assert info.start_tc == "01:00:00:00"
    assert abs(info.duration - 123.4) < 1e-6


def test_parse_probe_fallbacks() -> None:
    info = _parse_probe([], {})
    assert info.fps == 30.0
    assert (info.width, info.height) == (1920, 1080)
    assert info.start_tc is None
    assert info.duration == 0.0


# ---- 타임코드 프레임 변환 + EDL 시작 TC 오프셋 ----

def test_tc_frame_roundtrip() -> None:
    assert _tc_to_frames("01:00:00:00", 24) == 86400
    assert _frames_to_tc(86400, 24) == "01:00:00:00"
    assert _tc_to_frames("00:00:10:12", 24) == 252
    assert _frames_to_tc(252, 24) == "00:00:10:12"
    assert _tc_to_frames("garbage", 24) == 0  # 형식 오류는 0


def test_edl_applies_source_start_tc(tmp_path: Path) -> None:
    out = tmp_path / "t.edl"
    write_edl([], out, total_duration=20.0, fps=24.0, source_start_tc="01:00:00:00")
    text = out.read_text(encoding="utf-8")
    # 원본 시작 TC가 01:00:00:00이면 소스 인/아웃이 그만큼 밀려야 한다.
    assert "01:00:00:00 01:00:20:00" in text


# ---- 간투사 모드 분리 ----

def test_split_fillers_modes() -> None:
    cuts = [
        CutCandidate(start=0.0, end=0.3, kind="filler", reason="", text="음", confidence=0.9),
        CutCandidate(start=1.0, end=1.3, kind="filler", reason="", text="약간", confidence=0.6),
        CutCandidate(start=2.0, end=3.0, kind="silence", reason="무음", confidence=1.0),
    ]
    keep_c, cand_c = _split_fillers(cuts, "conservative")
    assert len(keep_c) == 2  # 핵심 간투사 + 무음
    assert [c.text for c in cand_c] == ["약간"]  # 애매한 간투사는 후보로

    keep_b, cand_b = _split_fillers(cuts, "balanced")
    assert len(keep_b) == 3 and cand_b == []  # 전부 자동 컷


# ---- 미리보기 리포트 ----

def test_write_review_report(tmp_path: Path) -> None:
    meta = Meta(
        source_path="/media/C0430.mp4", duration=100.0,
        subtitles=[SubtitleSegment(index=1, start=0.0, end=1.0, text="안녕")],
        cuts=[CutCandidate(start=10.0, end=12.0, kind="silence", reason="무음 2초")],
        filler_candidates=[
            CutCandidate(start=5.0, end=5.3, kind="filler", reason="", text="약간", confidence=0.6)
        ],
    )
    out = write_review(meta, tmp_path / "r.md", fps=23.976)
    text = out.read_text(encoding="utf-8")
    assert "컷 미리보기" in text
    assert "제거 예정" in text
    assert "검토 필요" in text  # 애매 간투사 섹션
    assert "약간" in text


# ---- 파이프라인 통합 (heavy 의존성 monkeypatch) ----

def test_pipeline_analyze_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "C0430.mp4"
    video.write_bytes(b"\x00")

    def fake_probe(_p: str | Path) -> MediaInfo:
        return MediaInfo(duration=10.0, fps=23.976, width=1920, height=1080, start_tc=None)

    def fake_extract(_v: str | Path, out: str | Path) -> Path:
        Path(out).write_bytes(b"\x00")
        return Path(out)

    def fake_iter(*_args: object, **_kwargs: object) -> Iterator[Segment]:
        yield Segment(
            start=0.0, end=2.0, text="음 오늘은",
            words=[Word(word="음", start=0.0, end=0.3, prob=0.9), Word(word="오늘은", start=0.4, end=1.0, prob=0.9)],
        )
        yield Segment(
            start=2.0, end=4.0, text="중요한 내용입니다",
            words=[Word(word="중요한", start=2.0, end=2.6, prob=0.9), Word(word="내용입니다", start=2.7, end=3.6, prob=0.9)],
        )

    captured_silence: dict[str, object] = {}

    def fake_silence(*_a: object, **kwargs: object) -> list[object]:
        captured_silence.update(kwargs)
        return []

    monkeypatch.setattr(audio, "probe_media", fake_probe)
    monkeypatch.setattr(audio, "extract_wav", fake_extract)
    monkeypatch.setattr(silence, "detect_silences", fake_silence)
    monkeypatch.setattr(transcribe, "iter_segments", fake_iter)

    out_dir = tmp_path / "out"
    result = pipeline.analyze(
        video, out_dir, model="tiny", device="cpu", compute_type="int8", filler_mode="balanced",
    )

    assert result.srt_path.exists()
    assert result.edl_path.exists()
    assert result.fcpxml_path is not None and result.fcpxml_path.exists()
    assert result.review_path.exists()
    assert result.json_path.exists()
    assert result.fps == 23.976
    assert any(c.kind == "filler" for c in result.meta.cuts)  # '음' 검출
    # v0.7 기본은 word timestamp가 있으면 RMS detect_silences 대신
    # 문맥 기반 word-gap 플래너를 사용한다. RMS는 word가 없을 때만 fallback.
    assert captured_silence == {}
    # keep_wav=False → 임시 WAV 정리됨
    assert result.wav_path is None
    assert not (out_dir / "C0430.kocut.wav").exists()


# ---- v0.4.1: CUDA DLL 경로 탐색 ----

def test_find_nvidia_bin_dirs_returns_str_list() -> None:
    from kocut.transcribe import _find_nvidia_bin_dirs
    result = _find_nvidia_bin_dirs()
    assert isinstance(result, list)
    assert all(isinstance(p, str) for p in result)


def test_register_nvidia_dll_dirs_no_crash() -> None:
    import kocut.transcribe as t
    t._nvidia_dll_registered = False
    t._register_nvidia_dll_dirs()  # 플랫폼 무관 예외 없이 통과해야 함
