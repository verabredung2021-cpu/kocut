"""v0.2.8.post1 회귀 테스트."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest

from kocut import transcribe
from kocut.cli import _normalise_args, _words_from_segments
from kocut.logger import get_logger
from kocut.output import write_edl
from kocut.types import Segment


def test_cli_accepts_direct_and_legacy_process_forms() -> None:
    assert _normalise_args(["video.mp4"]) == ["process", "video.mp4"]
    assert _normalise_args(["-m", "small", "video.mp4"]) == ["process", "-m", "small", "video.mp4"]
    assert _normalise_args(["process", "video.mp4"]) == ["process", "video.mp4"]
    assert _normalise_args(["--help"]) == ["--help"]


def test_words_from_segments_fallback_when_word_timestamps_missing() -> None:
    seg = Segment(start=1.0, end=3.0, text="세그먼트 자막", words=[])
    words, used_fallback = _words_from_segments([seg])
    assert used_fallback is True
    assert len(words) == 1
    assert words[0].word == "세그먼트 자막"
    assert words[0].start == 1.0
    assert words[0].end == 3.0


def test_write_edl_includes_video_and_audio_tracks(tmp_path: Path) -> None:
    out = tmp_path / "test.edl"
    write_edl([], out, total_duration=2.0, fps=30.0, source_name="clip.mp4")
    text = out.read_text(encoding="utf-8")
    assert " V     C " in text
    assert " A     C " in text
    assert "* FROM CLIP NAME: clip.mp4" in text


def test_logger_adds_late_file_handler(tmp_path: Path) -> None:
    name = f"kocut.test.{uuid.uuid4()}"
    logger = get_logger(name)
    assert isinstance(logger, logging.Logger)

    log_file = tmp_path / "late.log"
    logger = get_logger(name, log_file=log_file)
    logger.info("파일 핸들러 추가 확인")
    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists()
    assert "파일 핸들러 추가 확인" in log_file.read_text(encoding="utf-8")


def test_iter_segments_handles_raw_words_without_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    class RawWord:
        word = "안녕"
        start = 0.0
        end = 0.5

    class RawSegment:
        start = 0.0
        end = 1.0
        text = "안녕"
        words = [RawWord()]

    monkeypatch.setattr(transcribe, "_run_transcription", lambda *args, **kwargs: [RawSegment()])
    result = list(transcribe.iter_segments("dummy.wav", model_name="tiny", device="cpu", compute_type="int8"))
    assert result[0].text == "안녕"
    assert result[0].words[0].prob is None
