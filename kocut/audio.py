"""FFmpeg 래퍼.

영상에서 오디오를 추출하고 길이 등 메타데이터를 조회합니다. ffmpeg/ffprobe는
시스템 PATH에 있어야 합니다. 모든 오류는 한국어 메시지로 변환합니다.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    """FFmpeg 실행 실패."""


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise FFmpegError(
            f"'{tool}'를 찾을 수 없습니다. FFmpeg를 설치하고 PATH에 추가하세요. "
            "(Windows: winget install Gyan.FFmpeg / macOS: brew install ffmpeg)"
        )
    return path


def get_duration(media_path: str | Path) -> float:
    """미디어 길이를 초 단위로 반환합니다."""
    ffprobe = _require("ffprobe")
    media_path = Path(media_path)
    if not media_path.exists():
        raise FFmpegError(f"파일을 찾을 수 없습니다: {media_path}")
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe 실패: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
        return float(data["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise FFmpegError(f"길이 정보를 읽지 못했습니다: {exc}")


@dataclass(frozen=True)
class MediaInfo:
    """영상 메타데이터 (ffprobe로 조회)."""

    duration: float
    fps: float
    width: int
    height: int
    start_tc: str | None  # 원본 시작 타임코드 "HH:MM:SS:FF" (없으면 None)


def _parse_fraction(value: str, fallback: float) -> float:
    """'24000/1001' 같은 분수 문자열을 float로 (0 분모/형식 오류는 fallback)."""
    value = (value or "").strip()
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            n, d = float(num), float(den)
        except ValueError:
            return fallback
        return n / d if d else fallback
    try:
        return float(value)
    except ValueError:
        return fallback


def _parse_probe(streams: list[dict[str, object]], fmt: dict[str, object]) -> MediaInfo:
    """ffprobe JSON(스트림/포맷)을 MediaInfo로 변환합니다 (순수 함수, 테스트 용이)."""
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None and streams:
        video = streams[0]
    video = video or {}

    fps = _parse_fraction(str(video.get("r_frame_rate", "")), 0.0)
    if fps <= 0:
        fps = _parse_fraction(str(video.get("avg_frame_rate", "")), 0.0)
    if not (0 < fps < 1000):
        fps = 30.0

    def _int(value: object, default: int) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    width = _int(video.get("width"), 1920)
    height = _int(video.get("height"), 1080)

    start_tc = None
    for tags in (video.get("tags"), fmt.get("tags")):
        if isinstance(tags, dict):
            tc = tags.get("timecode")
            if isinstance(tc, str) and re.match(r"^\d{1,2}[:;]\d{2}[:;]\d{2}[:;]\d{2}$", tc.strip()):
                start_tc = tc.strip()
                break

    duration = 0.0
    raw_dur = fmt.get("duration")
    try:
        duration = float(raw_dur)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        duration = 0.0

    return MediaInfo(duration=duration, fps=fps, width=width, height=height, start_tc=start_tc)


def probe_media(media_path: str | Path) -> MediaInfo:
    """ffprobe로 영상의 길이/fps/해상도/시작 타임코드를 조회합니다.

    실패하거나 정보가 없으면 안전한 기본값(fps 30, 1920x1080, 시작 TC 없음)으로
    채웁니다. fps/해상도는 EDL·FCPXML 출력에, 시작 TC는 EDL relink 보정에 씁니다.
    """
    ffprobe = _require("ffprobe")
    media_path = Path(media_path)
    if not media_path.exists():
        raise FFmpegError(f"파일을 찾을 수 없습니다: {media_path}")
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_type,r_frame_rate,avg_frame_rate,width,height:stream_tags=timecode:"
        "format=duration:format_tags=timecode",
        "-of", "json", str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe 실패: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"미디어 정보를 읽지 못했습니다: {exc}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    info = _parse_probe(streams, fmt)
    # duration이 비면 기존 get_duration으로 보완
    if info.duration <= 0:
        try:
            info = MediaInfo(get_duration(media_path), info.fps, info.width, info.height, info.start_tc)
        except FFmpegError:
            pass
    return info


def extract_wav(video_path: str | Path, out_path: str | Path) -> Path:
    """영상에서 16kHz mono WAV를 추출합니다 (Whisper 입력용)."""
    ffmpeg = _require("ffmpeg")
    video_path = Path(video_path)
    out_path = Path(out_path)
    if not video_path.exists():
        raise FFmpegError(f"영상 파일을 찾을 수 없습니다: {video_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"오디오 추출 실패: {proc.stderr.strip()[-500:]}")
    if not out_path.exists():
        raise FFmpegError("오디오 추출이 끝났지만 출력 파일이 없습니다.")
    return out_path
