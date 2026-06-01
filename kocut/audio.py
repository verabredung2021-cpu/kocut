"""FFmpeg 래퍼.

영상에서 오디오를 추출하고 길이 등 메타데이터를 조회합니다. ffmpeg/ffprobe는
시스템 PATH에 있어야 합니다. 모든 오류는 한국어 메시지로 변환합니다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
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
