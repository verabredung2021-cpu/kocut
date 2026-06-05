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

_KIND_KR = {"filler": "간투사", "silence": "무음", "retake": "재촬영", "low_info": "저정보", "production": "제작멘트"}


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
    n_prod = sum(1 for c in cuts if c.kind == "production")

    lines = [
        f"# KoCut 컷 미리보기 — {Path(meta.source_path).name}",
        "",
        "## 요약",
        "",
        f"- 원본 길이: **{_tc(meta.duration)}** ({meta.duration:.1f}초)",
        f"- 제거 예정: **{_tc(removed)}** ({removed:.1f}초, {pct:.1f}%)",
        f"- 결과 길이: **{_tc(final)}** ({final:.1f}초)",
        f"- 컷 {len(cuts)}개 — 간투사 {n_fill} · 무음 {n_sil} · 재촬영 {n_ret} · 제작멘트 {n_prod}",
        f"- 프레임레이트: {fps:g}fps · 자막 {len(meta.subtitles)}줄 · 쇼츠 후보 {len(meta.shorts)}개",
        "- v0.9 정책: `이제`는 기본 삭제, `근데/그래서/그리고/그런데`는 연결어로 보호, 제작 멘트는 별도 자동 컷",
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

    if meta.review_candidates:
        lines += [
            "",
            "## 검토 필요 — 문장 단위 말실수/반복/저정보 후보",
            "",
            "_v0.8부터 자동 컷하지 않고 리뷰 CSV에 남깁니다. `*.review_decisions.csv`의 decision 열에 cut을 적은 뒤 `kocut apply-decisions`로 새 EDL을 만들 수 있습니다._",
            "",
            "| 시작 | 끝 | 길이 | 종류 | 신뢰도 | 이유 | 텍스트 |",
            "|---|---|---:|---|---:|---|---|",
            *[
                f"| {_tc(c.start)} | {_tc(c.end)} | {c.duration:.2f}s | {_KIND_KR.get(c.kind, c.kind)} | {c.confidence:.2f} | {c.reason} | {c.text.strip()} |"
                for c in sorted(meta.review_candidates, key=lambda c: c.start)
            ],
        ]

    if meta.topic_sections:
        lines += [
            "",
            "## 토픽/챕터 후보",
            "",
            "| # | 구간 | 제목 | 키워드 |",
            "|---:|---|---|---|",
            *[
                f"| {t.index} | {_tc(t.start)}–{_tc(t.end)} | {t.title} | {', '.join(t.keywords)} |"
                for t in meta.topic_sections
            ],
        ]

    if meta.utterances:
        lines += [
            "",
            "## Paper edit 요약",
            "",
            f"- 문장/호흡 단위: {len(meta.utterances)}개",
            "- 전체 목록은 `*.paper_edit.csv`와 `*.director_review.html`에서 확인하세요.",
        ]

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _fmt_seconds(seconds: float) -> str:
    return f"{max(0.0, seconds):.1f}s"


def write_variants_review(
    source_name: str,
    out_path: Path,
    *,
    duration: float,
    variants: dict[str, list[CutCandidate]],
) -> Path:
    """여러 컷 프리셋을 한 번에 비교하는 마크다운 리포트를 저장합니다.

    컷백처럼 프리셋을 바꿔가며 결과를 빠르게 비교할 수 있게, 같은 전사 결과에서
    safe/balanced/cutback/aggressive EDL을 동시에 만들고 수치를 보여줍니다.
    """
    from kocut import quality

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# KoCut 프리셋 비교 — {source_name}",
        "",
        "한 번의 전사 결과에서 여러 컷 강도를 동시에 만든 비교표입니다.",
        "Premiere/DaVinci에는 먼저 `safe` 또는 `balanced`를 넣고, 너무 느슨하면 `cutback`을 보세요.",
        "",
        "| 프리셋 | 판정 | 클립 수 | 컷 수 | 제거 시간 | 결과 길이 | 컷 중앙값 | 0.5초 미만 컷 | 2초 미만 클립 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cuts in variants.items():
        stats = quality.diagnose_cuts(cuts, duration)
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    stats.verdict,
                    str(stats.clips),
                    str(stats.cuts),
                    _fmt_seconds(stats.removed_seconds),
                    _fmt_seconds(stats.final_seconds),
                    _fmt_seconds(stats.median_cut_seconds),
                    str(stats.cuts_under_500ms),
                    str(stats.clips_under_2000ms),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## 읽는 법",
        "",
        "- `과분할 위험`이면 컷백 느낌이 아니라 자잘한 점프컷이 생길 가능성이 큽니다.",
        "- 상담/강의/인터뷰 longform은 보통 `safe` 또는 `balanced`가 시작점입니다.",
        "- 쇼츠/릴스처럼 빠른 템포가 목적일 때만 `aggressive`를 확인하세요.",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
