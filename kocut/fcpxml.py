"""FCPXML 출력 (DaVinci Resolve / Premiere relink용 — beta).

EDL의 HH:MM:SS:FF 타임코드 대신 **유리수 시간**(예: 1001/24000s)을 사용합니다.
덕분에 23.976·29.97 같은 분수 fps에서도 프레임 단위로 정확하고(드롭/논드롭
모호함 없음), 원본 파일 경로를 직접 담아 relink가 EDL보다 안정적입니다.

beta: 형식은 FCPXML 1.9를 따르지만 실제 Resolve/Premiere import는 별도 검증이
필요합니다.
"""
from __future__ import annotations

import math
from pathlib import Path
from urllib.request import pathname2url
from xml.sax.saxutils import escape

from kocut.output import _invert_cuts
from kocut.types import CutCandidate


def _frame_duration(fps: float) -> tuple[int, int]:
    """fps를 (분자, 분모) 초/프레임 유리수로 변환합니다 (예: 23.976 → 1001/24000)."""
    if not math.isfinite(fps) or fps <= 0:
        fps = 30.0
    n = round(fps)
    if n <= 1:
        n = 30
    # NTSC 분수 레이트 (n*1000/1001 에 가까우면)
    if abs(fps - n * 1000.0 / 1001.0) < 0.01:
        return 1001, n * 1000
    return 1, n


def _rational(frames: int, num: int, den: int) -> str:
    """프레임 수를 FCPXML 시간 문자열(N/Ds 또는 Ns)로 변환합니다."""
    total_num = frames * num
    if total_num == 0:
        return "0s"
    g = math.gcd(total_num, den)
    n, d = total_num // g, den // g
    return f"{n}s" if d == 1 else f"{n}/{d}s"


def _file_url(path: str) -> str:
    """Windows 경로(D:\\...)를 포함해 file:// URL을 만듭니다."""
    p = Path(path)
    try:
        return p.absolute().as_uri()
    except ValueError:
        return "file://localhost/" + pathname2url(str(p))


def write_fcpxml(
    cuts: list[CutCandidate],
    out_path: Path,
    total_duration: float,
    fps: float = 30.0,
    *,
    source_path: str = "source.mp4",
    width: int = 1920,
    height: int = 1080,
    min_clip_seconds: float = 0.0,
) -> Path:
    """컷을 반영한 FCPXML(1.9)을 저장합니다 (남길 구간만 spine에 배치)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    num, den = _frame_duration(fps)
    rate = den / num  # 정확한 유리수 fps

    keep = _invert_cuts(cuts, total_duration, min_keep=max(0.0, min_clip_seconds))
    total_frames = int(round(max(0.0, total_duration) * rate))
    name = Path(source_path).stem or "clip"
    frame_dur = f"{num}/{den}s"

    clips: list[str] = []
    rec_frames = 0
    for s, e in keep:
        src_f = int(round(s * rate))
        len_f = max(1, int(round((e - s) * rate)))
        clips.append(
            f'          <asset-clip ref="r2" offset="{_rational(rec_frames, num, den)}" '
            f'name="{escape(name)}" start="{_rational(src_f, num, den)}" '
            f'duration="{_rational(len_f, num, den)}" format="r1" tcFormat="NDF"/>'
        )
        rec_frames += len_f

    seq_dur = _rational(rec_frames, num, den)
    asset_dur = _rational(max(total_frames, 1), num, den)
    src_url = escape(_file_url(source_path))
    spine = "\n".join(clips)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">\n'
        "  <resources>\n"
        f'    <format id="r1" name="KoCutFormat" frameDuration="{frame_dur}" '
        f'width="{width}" height="{height}"/>\n'
        f'    <asset id="r2" name="{escape(name)}" start="0s" duration="{asset_dur}" '
        'hasVideo="1" hasAudio="1" audioSources="1" format="r1">\n'
        f'      <media-rep kind="original-media" src="{src_url}"/>\n'
        "    </asset>\n"
        "  </resources>\n"
        "  <library>\n"
        '    <event name="KoCut">\n'
        f'      <project name="{escape(name)} KoCut Edit">\n'
        f'        <sequence format="r1" duration="{seq_dur}" tcStart="0s" '
        'tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n'
        "          <spine>\n"
        f"{spine}\n"
        "          </spine>\n"
        "        </sequence>\n"
        "      </project>\n"
        "    </event>\n"
        "  </library>\n"
        "</fcpxml>\n"
    )
    out_path.write_text(xml, encoding="utf-8")
    return out_path
