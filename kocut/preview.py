"""컷 미리보기 렌더 (NLE 없이 결과 확인).

EDL을 NLE에 넣기 전에, 컷이 적용된 결과를 저해상도 MP4로 바로 렌더해서
"이 설정이면 어떻게 들리고 보이는지"를 즉시 확인합니다. Cutback류 상용 도구의
핵심 UX(자르기 전 미리 듣기)를 CLI로 제공하는 것입니다.

구현 메모: 남길 구간이 수십~수백 개면 ffmpeg filter_complex 문자열이 Windows
명령줄 길이 제한(8191자)을 넘으므로, 반드시 ``-filter_complex_script`` 파일로
전달합니다.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from kocut.audio import FFmpegError, _require


def build_filter_script(keeps: list[tuple[float, float]], *, height: int = 480) -> str:
    """trim/atrim + concat filtergraph 텍스트를 만듭니다 (순수 함수, 테스트 용이)."""
    lines: list[str] = []
    for i, (s, e) in enumerate(keeps):
        lines.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,scale=-2:{height}[v{i}];"
        )
        lines.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}];")
    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(keeps)))
    lines.append(f"{pairs}concat=n={len(keeps)}:v=1:a=1[outv][outa]")
    return "\n".join(lines)


def render_preview(
    video_path: str | Path,
    keeps: list[tuple[float, float]],
    out_path: str | Path,
    *,
    height: int = 480,
    crf: int = 28,
) -> Path:
    """남길 구간만 이어 붙인 미리보기 MP4를 렌더합니다."""
    if not keeps:
        raise FFmpegError("남길 구간이 없습니다 — EDL에 클립이 없거나 fps가 잘못됐을 수 있습니다.")
    ffmpeg = _require("ffmpeg")
    video_path = Path(video_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    script = build_filter_script(keeps, height=height)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".fgraph", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(script)
        script_path = Path(fh.name)
    try:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video_path),
            "-filter_complex_script", str(script_path),
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-c:a", "aac", "-movflags", "+faststart",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise FFmpegError(f"미리보기 렌더 실패: {proc.stderr.strip()[-400:]}")
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass
    return out_path
