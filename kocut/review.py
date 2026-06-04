"""컷 미리보기 리포트.

EDL/FCPXML을 NLE에 넣기 전에 "무엇이 어디서 왜 잘리는지"를 사람이 한눈에 훑을 수
있는 마크다운 리포트를 만듭니다. auto-editor 류 도구의 '컷 전/후 리포트' 아이디어를
한국어 초벌 검토 워크플로에 맞춘 것입니다.

- 상단 요약: 원본 길이 / 제거 시간 / 결과 길이 / 종류별 컷 수
- 컷 목록: 시작~끝, 종류, 길이, 이유/텍스트 (자동 적용 대상)
- 검토 필요(애매): --filler-mode에서 자동 컷에서 빠진 간투사 후보 — 직접 판단용
"""
from __future__ import annotations

from pathlib import Path

from kocut.types import CutCandidate, Meta

_KIND_KR = {"filler": "간투사", "silence": "무음", "retake": "재촬영", "low_info": "저정보"}


def _tc(seconds: float) -> str:
    """초를 MM:SS.s (1시간 넘으면 H:MM:SS.s)로."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m}:{s:04.1f}"


def _row(cut: CutCandidate) -> str:
    kind = _KIND_KR.get(cut.kind, cut.kind)
    detail = cut.text.strip() or cut.reason
    return f"| {_tc(cut.start)} | {_tc(cut.end)} | {cut.duration:.2f}s | {kind} | {detail} |"


def write_review(meta: Meta, out_path: Path, *, fps: float = 30.0) -> Path:
    """컷 미리보기 마크다운 리포트를 저장합니다."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cuts = sorted(meta.cuts, key=lambda c: c.start)
    removed = sum(c.duration for c in cuts)
    final = max(0.0, meta.duration - removed)
    pct = (removed / meta.duration * 100.0) if meta.duration > 0 else 0.0
    n_fill = sum(1 for c in cuts if c.kind == "filler")
    n_sil = sum(1 for c in cuts if c.kind == "silence")
    n_ret = sum(1 for c in cuts if c.kind == "retake")

    lines = [
        f"# KoCut 컷 미리보기 — {Path(meta.source_path).name}",
        "",
        "## 요약",
        "",
        f"- 원본 길이: **{_tc(meta.duration)}** ({meta.duration:.1f}초)",
        f"- 제거 예정: **{_tc(removed)}** ({removed:.1f}초, {pct:.1f}%)",
        f"- 결과 길이: **{_tc(final)}** ({final:.1f}초)",
        f"- 컷 {len(cuts)}개 — 간투사 {n_fill} · 무음 {n_sil} · 재촬영 {n_ret}",
        f"- 프레임레이트: {fps:g}fps · 자막 {len(meta.subtitles)}줄 · 쇼츠 후보 {len(meta.shorts)}개",
    ]
    for w in meta.warnings:
        lines.append(f"- ⚠️ {w}")

    lines += ["", "## 자동 컷 목록", ""]
    if cuts:
        lines += [
            "| 시작 | 끝 | 길이 | 종류 | 내용/이유 |",
            "|---|---|---|---|---|",
            *[_row(c) for c in cuts],
        ]
    else:
        lines.append("_컷 후보 없음._")

    if meta.filler_candidates:
        lines += [
            "",
            "## 검토 필요 — 애매한 간투사 (자동 컷에서 제외됨)",
            "",
            "_문맥상 의미가 있을 수 있어 자동으로 자르지 않았습니다. 직접 확인 후 EDL에 반영하세요._",
            "",
            "| 시작 | 끝 | 길이 | 단어 |",
            "|---|---|---|---|",
            *[
                f"| {_tc(c.start)} | {_tc(c.end)} | {c.duration:.2f}s | {c.text.strip() or c.reason} |"
                for c in sorted(meta.filler_candidates, key=lambda c: c.start)
            ],
        ]

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
