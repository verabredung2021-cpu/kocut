"""Premiere용 FCP7 XML(xmeml) 출력.

중요 정정: Premiere Pro는 ``.fcpxml``(Final Cut Pro X 포맷)을 **import하지 못합니다**.
Premiere가 읽는 것은 'Final Cut Pro XML' = FCP7 시절의 **xmeml** 포맷입니다.
(DaVinci Resolve는 둘 다 읽습니다.) 이 모듈은 Premiere 워크플로용 xmeml을 만듭니다.

EDL 대비 장점:
- ``pathurl``로 원본 파일 경로를 직접 담아 relink가 자동에 가깝습니다.
- 프레임 수를 실제 fps(예: 23.976 = timebase 24 + ntsc TRUE)로 세서 정확합니다.
- 비디오/오디오 트랙이 명시적으로 분리됩니다.
"""
from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from kocut.output import _invert_cuts
from kocut.types import CutCandidate


def _rate(fps: float) -> tuple[int, bool, float]:
    """fps → (timebase, ntsc 여부, 실제 fps). 23.976→(24, True, 23.976...)."""
    if not math.isfinite(fps) or fps <= 1.0:
        return 30, False, 30.0
    base = int(round(fps))
    if base <= 1:
        return 30, False, 30.0
    ntsc = abs(fps - base * 1000.0 / 1001.0) < 0.01
    real = base * 1000.0 / 1001.0 if ntsc else float(base)
    return base, ntsc, real


def _pathurl(source_path: str) -> str:
    """Windows/유닉스 경로를 Premiere가 읽는 file://localhost/ URL로 변환합니다."""
    norm = str(source_path).replace("\\", "/").lstrip("/")
    return "file://localhost/" + quote(norm, safe="/:")


def _rate_xml(timebase: int, ntsc: bool, indent: str) -> str:
    flag = "TRUE" if ntsc else "FALSE"
    return f"{indent}<rate><timebase>{timebase}</timebase><ntsc>{flag}</ntsc></rate>"


def write_xmeml(
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
    """컷을 반영한 FCP7 XML(xmeml v4) 시퀀스를 저장합니다 (남길 구간만)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timebase, ntsc, real_fps = _rate(fps)

    def fr(seconds: float) -> int:
        return max(0, int(round(seconds * real_fps)))

    keep = _invert_cuts(cuts, total_duration, min_keep=max(0.0, min_clip_seconds))
    name = escape(Path(source_path).name or "source")
    url = escape(_pathurl(source_path))
    total_f = max(1, fr(max(0.0, total_duration)))
    rate_seq = _rate_xml(timebase, ntsc, "    ")
    rate_item = _rate_xml(timebase, ntsc, "              ")

    file_full = (
        f'              <file id="file-1">\n'
        f"                <name>{name}</name>\n"
        f"                <pathurl>{url}</pathurl>\n"
        f"{_rate_xml(timebase, ntsc, '                ')}\n"
        f"                <duration>{total_f}</duration>\n"
        f"                <media>\n"
        f"                  <video><samplecharacteristics>"
        f"<width>{width}</width><height>{height}</height>"
        f"</samplecharacteristics></video>\n"
        f"                  <audio><channelcount>2</channelcount></audio>\n"
        f"                </media>\n"
        f"              </file>"
    )
    file_ref = '              <file id="file-1"/>'

    def clipitem(idx: int, kind: str, s: float, e: float, rec: int, first: bool) -> str:
        dur = max(1, fr(e) - fr(s))
        src_in, src_out = fr(s), fr(s) + dur
        body = file_full if first else file_ref
        extra = (
            "\n              <sourcetrack><mediatype>audio</mediatype>"
            "<trackindex>1</trackindex></sourcetrack>"
            if kind == "a"
            else ""
        )
        return (
            f'            <clipitem id="clipitem-{kind}-{idx}">\n'
            f"              <name>{name}</name>\n"
            f"              <enabled>TRUE</enabled>\n"
            f"{rate_item}\n"
            f"              <start>{rec}</start>\n"
            f"              <end>{rec + dur}</end>\n"
            f"              <in>{src_in}</in>\n"
            f"              <out>{src_out}</out>\n"
            f"{body}{extra}\n"
            f"            </clipitem>"
        )

    v_items: list[str] = []
    a_items: list[str] = []
    rec = 0
    for i, (s, e) in enumerate(keep, start=1):
        v_items.append(clipitem(i, "v", s, e, rec, first=(i == 1)))
        a_items.append(clipitem(i, "a", s, e, rec, first=False))
        rec += max(1, fr(e) - fr(s))

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE xmeml>\n"
        '<xmeml version="4">\n'
        "  <sequence>\n"
        f"    <name>{escape(Path(source_path).stem)} KoCut Edit</name>\n"
        f"    <duration>{rec}</duration>\n"
        f"{rate_seq}\n"
        "    <media>\n"
        "      <video>\n"
        "        <format><samplecharacteristics>\n"
        f"{_rate_xml(timebase, ntsc, '          ')}\n"
        f"          <width>{width}</width><height>{height}</height>\n"
        "        </samplecharacteristics></format>\n"
        "        <track>\n" + "\n".join(v_items) + "\n        </track>\n"
        "      </video>\n"
        "      <audio>\n"
        "        <track>\n" + "\n".join(a_items) + "\n        </track>\n"
        "      </audio>\n"
        "    </media>\n"
        "  </sequence>\n"
        "</xmeml>\n"
    )
    out_path.write_text(xml, encoding="utf-8")
    return out_path
