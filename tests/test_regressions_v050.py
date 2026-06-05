"""v0.5.0 회귀 테스트 — repair-edl, 재촬영 오검출 수정, 자막 정리 확장.

ChatGPT의 C0433 복구 결과에서 배운 점들을 반영했습니다.
"""
from __future__ import annotations

from kocut.output import repair_edl
from kocut.retakes import detect_retakes
from kocut.subtitles import _clean_text
from kocut.types import Segment


def _tc24(f: int) -> str:
    return f"{f // (24 * 3600):02d}:{(f // (24 * 60)) % 60:02d}:{(f // 24) % 60:02d}:{f % 24:02d}"


def _make_edl(ranges: list[tuple[int, int]]) -> str:
    """프레임 범위 리스트(@24fps)로 KoCut 형식 EDL 텍스트를 만듭니다."""
    lines = ["TITLE: KoCut Edit", "FCM: NON-DROP FRAME", ""]
    rec = 0
    for i, (a, b) in enumerate(ranges, start=1):
        for tr in ("V", "A"):
            lines.append(
                f"{i:03d}  AX       {tr:<5} C        "
                f"{_tc24(a)} {_tc24(b)} {_tc24(rec)} {_tc24(rec + (b - a))}"
            )
            lines.append("* FROM CLIP NAME: C0433.mp4")
            lines.append("* SOURCE FILE: C0433.mp4")
        rec += b - a
    lines.append("")
    return "\n".join(lines)


# ---- repair-edl (짧은 갭 되살려 병합) ----

def test_repair_edl_merges_short_gaps() -> None:
    # A[0~120] -갭6프레임- B[126~240] -갭48프레임- C[288~360]
    edl = _make_edl([(0, 120), (126, 240), (288, 360)])
    repaired, before, after = repair_edl(edl, fps=24.0, min_gap_seconds=0.65)
    assert before == 3
    assert after == 2  # 짧은 갭(0.25s)으로 A+B 병합, 긴 갭(2s)은 유지
    assert "00:00:00:00 00:00:10:00" in repaired  # 병합 클립 A 시작 ~ B 끝
    assert "Repaired" in repaired


def test_repair_edl_drops_short_clip() -> None:
    edl = _make_edl([(0, 3), (120, 240)])  # 첫 클립 3프레임, 갭은 김
    _repaired, before, after = repair_edl(
        edl, fps=24.0, min_gap_seconds=0.65, min_clip_seconds=0.5
    )
    assert before == 2
    assert after == 1  # 0.125s 클립은 min_clip 0.5s 미만 → 제거


def test_repair_edl_empty_input() -> None:
    _repaired, before, after = repair_edl("쓰레기 텍스트", fps=24.0)
    assert before == 0 and after == 0


# ---- 재촬영 오검출 수정 ----

def test_retake_ignores_conversational_dasi() -> None:
    # "다시"는 일상어 — 단독으로는 컷하지 않아야 한다.
    segs = [Segment(start=0.0, end=3.0, text="최상에서 다시 해보니까 1.2가 나온 거예요", words=[])]
    assert detect_retakes(segs) == []


def test_retake_detects_explicit_restart_phrase() -> None:
    # "다시 갈게요" 같은 촬영 재시작 구문은 컷.
    segs = [Segment(start=0.0, end=2.0, text="아 잠시만요 다시 갈게요", words=[])]
    cuts = detect_retakes(segs)
    assert any(c.kind == "retake" for c in cuts)


# ---- 자막 정리 확장 (퍼센트/마침표 앞 공백) ----

def test_clean_text_percent_and_trailing_period() -> None:
    assert _clean_text("확률이 5 % 10 %라고") == "확률이 5% 10%라고"
    assert _clean_text("적용할 수 있기 때문에 .") == "적용할 수 있기 때문에."
    assert _clean_text("2025. 5월에") == "2025. 5월에"  # 문장 경계는 보존
