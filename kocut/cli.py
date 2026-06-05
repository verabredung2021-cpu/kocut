"""KoCut 명령줄 인터페이스.

다음 두 형식을 모두 지원합니다:
- 새/간단 형식: ``python -m kocut video.mp4``
- 하위 명령 형식: ``python -m kocut process video.mp4``

전체 파이프라인은 오디오 추출 → 한국어 트랜스크립션 → 자막 분할 → 컷 후보
검출 → 파일 출력 순서로 실행됩니다.
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Iterable

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from kocut import output, pipeline, quality
from kocut.audio import FFmpegError
from kocut.logger import get_logger
from kocut.pipeline import FILLER_MODES
from kocut.transcribe import WhisperNotInstalledError
from kocut.types import CutCandidate, Meta, Segment, Word

app = typer.Typer(
    add_completion=False,
    help="KoCut — 한국어 영상 자동 편집 보조 도구",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

_COMMANDS = {"process", "repair-edl", "diagnose-edl", "apply-decisions"}
_ROOT_HELP_OPTIONS = {"-h", "--help", "--install-completion", "--show-completion"}
_VALID_DEVICES = {"auto", "cuda", "cpu"}
_VALID_COMPUTE_TYPES = {
    "auto",
    "default",
    "float16",
    "float32",
    "int8",
    "int8_float16",
    "int8_bfloat16",
    "int16",
    "bfloat16",
}


@app.callback()
def _root() -> None:
    """하위 명령 그룹을 유지하기 위한 루트 콜백입니다."""


def _normalise_args(args: Iterable[str]) -> list[str]:
    """기존/신규 실행 형식을 모두 받도록 argv를 정규화합니다."""
    argv = list(args)
    if not argv:
        return argv
    first = argv[0]
    if first in _COMMANDS or first in _ROOT_HELP_OPTIONS:
        return argv
    return ["process", *argv]


def _validate_device(device: str) -> str:
    value = (device or "auto").strip().lower()
    if value not in _VALID_DEVICES:
        allowed = " / ".join(sorted(_VALID_DEVICES))
        raise typer.BadParameter(f"처리 장치는 {allowed} 중 하나여야 합니다.")
    return value


def _validate_compute_type(compute_type: str) -> str:
    value = (compute_type or "auto").strip()
    if value not in _VALID_COMPUTE_TYPES:
        allowed = " / ".join(sorted(_VALID_COMPUTE_TYPES))
        raise typer.BadParameter(f"compute type은 {allowed} 중 하나여야 합니다.")
    return value


def _words_from_segments(segments: list[Segment]) -> tuple[list[Word], bool]:
    """세그먼트 → 단어 리스트 (pipeline 로직 재사용; 회귀 테스트 호환용)."""
    return pipeline.words_from_segments(segments)


def _run_pipeline(
    video: Path,
    out_dir: Path,
    log_path: Path,
    model: str,
    device: str,
    compute_type: str,
    fps: float | None,
    skip_fillers: bool,
    skip_silence: bool,
    skip_retakes: bool,
    skip_shorts: bool,
    keep_wav: bool,
    verbose: bool,
    *,
    cut_preset: str = "safe",
    pad_before_ms: int | None = None,
    pad_after_ms: int | None = None,
    min_cut_ms: int | None = None,
    min_clip_ms: int | None = None,
    min_silence_ms: int | None = None,
    min_keep_between_cuts_ms: int | None = None,
    filler_mode: str | None = None,
    skip_fcpxml: bool = False,
    write_variants: bool = True,
    director_mode: bool = True,
) -> None:
    """공용 파이프라인(pipeline.analyze)을 호출하고 결과를 콘솔에 요약합니다."""
    logger = get_logger("kocut", log_file=log_path, verbose=verbose)
    device = _validate_device(device)
    compute_type = _validate_compute_type(compute_type)

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("준비 중...", total=1.0)

        def on_progress(fraction: float, desc: str) -> None:
            progress.update(task, completed=fraction, description=desc)

        result = pipeline.analyze(
            video,
            out_dir,
            model=model,
            device=device,
            compute_type=compute_type,
            fps=fps,
            skip_fillers=skip_fillers,
            skip_silence=skip_silence,
            skip_retakes=skip_retakes,
            skip_shorts=skip_shorts,
            skip_fcpxml=skip_fcpxml,
            keep_wav=keep_wav,
            cut_preset=cut_preset,
            pad_before_ms=pad_before_ms,
            pad_after_ms=pad_after_ms,
            min_cut_ms=min_cut_ms,
            min_clip_ms=min_clip_ms,
            min_silence_ms=min_silence_ms,
            min_keep_between_cuts_ms=min_keep_between_cuts_ms,
            filler_mode=filler_mode,
            write_variants=write_variants,
            director_mode=director_mode,
            on_progress=on_progress,
            logger=logger,
        )

    meta = result.meta
    if result.used_word_fallback:
        console.print("[yellow]경고: word timestamp가 없어 세그먼트 단위 자막으로 보강했습니다.[/yellow]")
    if not meta.subtitles and not meta.segments:
        console.print("[yellow]경고: 음성을 인식하지 못했습니다. 영상에 한국어 음성이 있는지 확인하세요.[/yellow]")

    stats = quality.diagnose_cuts(meta.cuts, meta.duration)
    table = Table(title="처리 완료")
    table.add_column("항목")
    table.add_column("값", justify="right")
    table.add_row("영상 길이", f"{meta.duration:.1f}초")
    table.add_row("프레임레이트", f"{result.fps:g}fps")
    table.add_row("품질 프리셋", cut_preset)
    table.add_row("품질 판정", stats.verdict)
    table.add_row("자막", f"{len(meta.subtitles)}줄")
    table.add_row("문장 단위", f"{len(meta.utterances)}개")
    table.add_row("리뷰 후보", f"{len(meta.review_candidates) + len(meta.filler_candidates)}개")
    table.add_row("간투사 컷", str(sum(1 for c in meta.cuts if c.kind == "filler")))
    table.add_row("무음 컷", str(sum(1 for c in meta.cuts if c.kind == "silence")))
    table.add_row("재촬영 컷", str(sum(1 for c in meta.cuts if c.kind == "retake")))
    if meta.filler_candidates:
        table.add_row("검토 후보(애매 간투사)", str(len(meta.filler_candidates)))
    table.add_row("제거 시간", f"{stats.removed_seconds:.1f}초")
    table.add_row("결과 길이", f"{stats.final_seconds:.1f}초")
    table.add_row("쇼츠 후보", str(len(meta.shorts)))
    console.print(table)

    if stats.verdict == "과분할 위험":
        console.print("[yellow]경고: 컷이 과분할될 가능성이 큽니다. --cut-preset safe 또는 --write-variants 결과를 먼저 보세요.[/yellow]")

    console.print("\n[green]생성된 파일:[/green]")
    console.print(f"  자막:        {result.srt_path}")
    console.print(f"  컷 EDL:      {result.edl_path}")
    if result.fcpxml_path is not None:
        console.print(f"  컷 FCPXML:   {result.fcpxml_path}  [dim](beta)[/dim]")
    console.print(f"  미리보기:    {result.review_path}")
    console.print(f"  Director HTML: {result.html_review_path}")
    console.print(f"  Paper edit CSV: {result.paper_edit_path}")
    console.print(f"  리뷰 결정 CSV: {result.review_candidates_path}")
    if result.variant_edl_paths:
        console.print("  프리셋 EDL:")
        for name, path in result.variant_edl_paths.items():
            console.print(f"    - {name}: {path}")
    if result.variants_report_path is not None:
        console.print(f"  프리셋 리포트: {result.variants_report_path}")
    console.print(f"  메타데이터:  {result.json_path}")
    if result.wav_path is not None:
        console.print(f"  검사용 WAV:  {result.wav_path}")
    console.print("\nPremiere/DaVinci에서 SRT는 자막으로, EDL/FCPXML은 시퀀스로 import 하세요.")
    console.print("[dim]relink가 어긋나면 FCPXML(프레임 정확·경로 포함)을 먼저 시도하세요. 컷 적용 전 .cuts.md로 검토.[/dim]")


@app.command("process")
def process(
    video: Path = typer.Argument(..., help="처리할 영상 파일 경로"),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="출력 폴더 (기본: 영상과 같은 폴더)"),
    model: str = typer.Option("large-v3", "--model", "-m", help="Whisper 모델 이름"),
    device: str = typer.Option("auto", "--device", "-d", help="처리 장치: auto / cuda / cpu"),
    compute_type: str = typer.Option("auto", "--compute-type", help="Whisper compute type: auto / float16 / int8 등"),
    fps: float | None = typer.Option(None, "--fps", help="EDL/FCPXML 프레임레이트 (미지정 시 원본에서 자동 감지)"),
    cut_preset: str = typer.Option(
        "safe", "--cut-preset",
        help="컷 품질 프리셋: safe / balanced / cutback / aggressive",
    ),
    filler_mode: str | None = typer.Option(
        None, "--filler-mode",
        help="간투사 적용 강도: conservative / balanced / aggressive (기본: 프리셋 값)",
    ),
    skip_fillers: bool = typer.Option(False, "--skip-fillers", help="간투사 검출 건너뛰기"),
    skip_silence: bool = typer.Option(False, "--skip-silence", help="무음 검출 건너뛰기"),
    detect_retakes: bool = typer.Option(False, "--detect-retakes", help="재촬영/NG 자동 컷 켜기 (기본 꺼짐)"),
    skip_retakes: bool = typer.Option(False, "--skip-retakes", help="호환용 옵션: 재촬영 검출 끄기"),
    skip_shorts: bool = typer.Option(False, "--skip-shorts", help="쇼츠 후보 건너뛰기"),
    keep_wav: bool = typer.Option(False, "--keep-wav", help="Whisper 입력용 WAV를 삭제하지 않고 보관"),
    pad_before_ms: int | None = typer.Option(None, "--pad-before-ms", help="다음 발화 시작 전 남길 여유(ms) — 미지정 시 프리셋 값"),
    pad_after_ms: int | None = typer.Option(None, "--pad-after-ms", help="직전 발화 끝난 뒤 남길 여유(ms) — 미지정 시 프리셋 값"),
    min_silence_ms: int | None = typer.Option(None, "--min-silence-ms", help="이보다 긴 발화 사이 gap만 컷 — 미지정 시 프리셋 값"),
    min_cut_ms: int | None = typer.Option(None, "--min-cut-ms", help="이보다 짧은 삭제 컷은 버림 — 미지정 시 프리셋 값"),
    min_keep_between_cuts_ms: int | None = typer.Option(None, "--min-keep-between-cuts-ms", help="두 컷 사이 고립 클립 최소 길이 — 미지정 시 프리셋 값"),
    min_clip_ms: int | None = typer.Option(None, "--min-clip-ms", help="EDL 출력에서 이보다 짧은 남길 구간 제거(기본 0, 보통 쓰지 않음)"),
    skip_fcpxml: bool = typer.Option(False, "--skip-fcpxml", help="FCPXML 출력 건너뛰기"),
    write_variants: bool = typer.Option(True, "--write-variants/--no-write-variants", help="safe/balanced/cutback/aggressive EDL을 한 번에 추가 출력"),
    director_mode: bool = typer.Option(True, "--director-mode/--word-gap-mode", help="문장 단위 컷 엔진 사용(v0.8 기본). 끄면 v0.7 word-gap 방식"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="상세 로그"),
) -> None:
    """영상을 분석해 자막(SRT) + 컷 후보(EDL/FCPXML) + 메타데이터(JSON)를 생성합니다."""
    if cut_preset not in quality.preset_names():
        console.print(f"[red]옵션 오류:[/red] --cut-preset은 {' / '.join(quality.preset_names())} 중 하나여야 합니다.")
        raise typer.Exit(code=2)
    if filler_mode is not None and filler_mode not in FILLER_MODES:
        console.print(f"[red]옵션 오류:[/red] --filler-mode는 {' / '.join(FILLER_MODES)} 중 하나여야 합니다.")
        raise typer.Exit(code=2)
    video = video.expanduser().resolve()
    if not video.exists():
        console.print(f"[red]영상 파일을 찾을 수 없습니다:[/red] {video}")
        raise typer.Exit(code=1)

    out_dir = (output_dir or video.parent).expanduser().resolve()
    if out_dir.exists() and not out_dir.is_dir():
        console.print(f"[red]출력 경로가 폴더가 아닙니다:[/red] {out_dir}")
        raise typer.Exit(code=1)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{video.stem}.log"

    console.print(f"[bold cyan]KoCut[/bold cyan] 처리 시작: [bold]{video.name}[/bold]")

    try:
        _run_pipeline(
            video, out_dir, log_path, model, device, compute_type, fps,
            skip_fillers, skip_silence, (skip_retakes or not detect_retakes), skip_shorts, keep_wav, verbose,
            cut_preset=cut_preset,
            pad_before_ms=pad_before_ms,
            pad_after_ms=pad_after_ms,
            min_cut_ms=min_cut_ms,
            min_clip_ms=min_clip_ms,
            min_silence_ms=min_silence_ms,
            min_keep_between_cuts_ms=min_keep_between_cuts_ms,
            filler_mode=filler_mode,
            skip_fcpxml=skip_fcpxml,
            write_variants=write_variants,
            director_mode=director_mode,
        )
    except FFmpegError as exc:
        console.print(f"\n[red]오디오 처리 실패:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except WhisperNotInstalledError as exc:
        console.print(f"\n[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except typer.BadParameter as exc:
        console.print(f"\n[red]옵션 오류:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except KeyboardInterrupt as exc:
        console.print("\n[yellow]사용자가 중단했습니다.[/yellow]")
        raise typer.Exit(code=130) from exc
    except Exception as exc:  # noqa: BLE001  (사용자에게 친절한 메시지를 위해 광범위 캐치)
        logger = get_logger("kocut", log_file=log_path, verbose=verbose)
        logger.exception("처리 중 예상치 못한 오류")
        console.print(f"\n[red]예상치 못한 오류:[/red] {exc}")
        console.print(f"[dim]자세한 내용은 로그를 확인하세요: {log_path}[/dim]")
        raise typer.Exit(code=1) from exc


@app.command("repair-edl")
def repair_edl_command(
    edl: Path = typer.Argument(..., help="복구할 EDL 파일 경로"),
    out: Path | None = typer.Option(None, "-o", "--output", help="출력 EDL (기본: <원본>.repaired.edl)"),
    min_gap_ms: int = typer.Option(1000, "--min-gap-ms", help="이보다 짧은 삭제 구간은 되살려 인접 클립 병합 (클수록 적게 자름, 기본 1000)"),
    min_clip_ms: int = typer.Option(0, "--min-clip-ms", help="이보다 짧은 클립은 제거(ms)"),
    fps: float = typer.Option(23.976, "--fps", help="EDL 타임코드 fps (ms↔프레임 변환용)"),
) -> None:
    """과분할된 기존 EDL을 복구합니다 — 짧은 삭제 구간을 되살려 병합 (재분석/GPU 불필요)."""
    edl = edl.expanduser().resolve()
    if not edl.exists():
        console.print(f"[red]EDL 파일을 찾을 수 없습니다:[/red] {edl}")
        raise typer.Exit(code=1)
    text = edl.read_text(encoding="utf-8", errors="replace")
    repaired, before, after = output.repair_edl(
        text,
        fps=fps,
        min_gap_seconds=max(0, min_gap_ms) / 1000.0,
        min_clip_seconds=max(0, min_clip_ms) / 1000.0,
    )
    if before == 0:
        console.print("[yellow]EDL에서 클립을 찾지 못했습니다. KoCut이 생성한 EDL인지 확인하세요.[/yellow]")
        raise typer.Exit(code=1)
    out_path = out.expanduser() if out is not None else edl.with_name(f"{edl.stem}.repaired.edl")
    out_path.write_text(repaired, encoding="utf-8")
    console.print(f"[green]복구 완료:[/green] 클립 {before}개 → {after}개")
    console.print(f"  출력: {out_path}")
    console.print(f"  [dim]min-gap {min_gap_ms}ms 미만 삭제 구간을 되살림 · fps {fps:g}[/dim]")


@app.command("diagnose-edl")
def diagnose_edl_command(
    edl: Path = typer.Argument(..., help="진단할 EDL 파일 경로"),
    fps: float = typer.Option(23.976, "--fps", help="EDL 타임코드 fps"),
) -> None:
    """EDL 과분할 상태를 수치로 진단합니다."""

    def tc_to_sec(tc: str) -> float:
        base = int(round(fps)) if fps > 0 else 30
        h, m, s, f = (int(x) for x in re.split(r"[:;]", tc))
        return h * 3600 + m * 60 + s + f / float(fps if fps > 0 else base)

    edl = edl.expanduser().resolve()
    if not edl.exists():
        console.print(f"[red]EDL 파일을 찾을 수 없습니다:[/red] {edl}")
        raise typer.Exit(code=1)
    text = edl.read_text(encoding="utf-8", errors="replace")
    events = re.findall(
        r"^\d{3}\s+\S+\s+V\s+C\s+(\d\d:\d\d:\d\d[:;]\d\d)\s+(\d\d:\d\d:\d\d[:;]\d\d)",
        text,
        re.M,
    )
    if not events:
        console.print("[yellow]V 트랙 이벤트를 찾지 못했습니다.[/yellow]")
        raise typer.Exit(code=1)
    ranges = [(tc_to_sec(a), tc_to_sec(b)) for a, b in events]
    durs = [max(0.0, b - a) for a, b in ranges]
    gaps = [ranges[i][0] - ranges[i - 1][1] for i in range(1, len(ranges))]
    gaps = [g for g in gaps if g > 1e-6]
    removed = sum(gaps)

    table = Table(title=f"EDL 진단: {edl.name}")
    table.add_column("항목")
    table.add_column("값", justify="right")
    table.add_row("클립 수", str(len(ranges)))
    table.add_row("삭제 gap 수", str(len(gaps)))
    table.add_row("삭제 합계", f"{removed:.1f}초")
    table.add_row("클립 중앙값", f"{statistics.median(durs):.2f}초")
    if gaps:
        table.add_row("삭제 gap 중앙값", f"{statistics.median(gaps):.2f}초")
        table.add_row("0.5초 미만 gap", str(sum(g < 0.5 for g in gaps)))
        table.add_row("0.8초 미만 gap", str(sum(g < 0.8 for g in gaps)))
        table.add_row("1.0초 미만 gap", str(sum(g < 1.0 for g in gaps)))
    table.add_row("1초 미만 클립", str(sum(d < 1.0 for d in durs)))
    table.add_row("2초 미만 클립", str(sum(d < 2.0 for d in durs)))
    console.print(table)
    if len(ranges) > 120 or (gaps and statistics.median(gaps) < 0.6):
        console.print("[yellow]판정: 과분할 가능성이 큽니다. --cut-preset safe 또는 repair-edl --min-gap-ms 1000부터 보세요.[/yellow]")



_DECISION_CUT = {"cut", "yes", "y", "1", "true", "삭제", "자르기"}
_DECISION_KEEP = {"keep", "no", "n", "0", "false", "보류", "유지", "살림"}


def _float_cell(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip())
    except ValueError:
        return default


def _overlaps(a: CutCandidate, b: CutCandidate, *, tolerance: float = 0.05) -> bool:
    return a.start < b.end - tolerance and b.start < a.end - tolerance


def _decision_cuts_from_csv(path: Path) -> tuple[list[CutCandidate], list[CutCandidate], int]:
    """review_decisions.csv에서 cut/keep 결정을 읽습니다."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(text.splitlines())
    to_add: list[CutCandidate] = []
    to_keep: list[CutCandidate] = []
    rows = 0
    for row in reader:
        rows += 1
        decision = str(row.get("decision") or "").strip().lower()
        if not decision:
            continue
        start = _float_cell(row.get("start"))
        end = _float_cell(row.get("end"))
        if end <= start:
            continue
        cut = CutCandidate(
            start=start,
            end=end,
            kind=str(row.get("kind") or "review"),
            reason=str(row.get("reason") or "review decision"),
            text=str(row.get("text") or ""),
            confidence=_float_cell(row.get("confidence"), 1.0),
        )
        if decision in _DECISION_CUT:
            to_add.append(cut)
        elif decision in _DECISION_KEEP:
            to_keep.append(cut)
    return to_add, to_keep, rows


@app.command("apply-decisions")
def apply_decisions_command(
    meta_json: Path = typer.Argument(..., help="KoCut이 생성한 *.meta.json"),
    decisions_csv: Path = typer.Argument(..., help="*.review_decisions.csv — decision 열에 cut/keep 입력"),
    out: Path | None = typer.Option(None, "-o", "--output", help="출력 EDL (기본: <원본>.decisions.edl)"),
    fps: float = typer.Option(23.976, "--fps", help="출력 EDL fps"),
    base_auto_cuts: bool = typer.Option(True, "--base-auto-cuts/--review-only", help="기존 자동 컷을 포함하고 결정 CSV를 추가 적용"),
    source_name: str | None = typer.Option(None, "--source-name", help="EDL SOURCE FILE 이름 강제 지정"),
) -> None:
    """리뷰 CSV의 결정(cut/keep)을 반영해 새 EDL을 만듭니다."""
    meta_json = meta_json.expanduser().resolve()
    decisions_csv = decisions_csv.expanduser().resolve()
    if not meta_json.exists():
        console.print(f"[red]meta JSON을 찾을 수 없습니다:[/red] {meta_json}")
        raise typer.Exit(code=1)
    if not decisions_csv.exists():
        console.print(f"[red]decision CSV를 찾을 수 없습니다:[/red] {decisions_csv}")
        raise typer.Exit(code=1)
    try:
        meta = Meta.model_validate(json.loads(meta_json.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]meta JSON을 읽을 수 없습니다:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    add_cuts, keep_cuts, rows = _decision_cuts_from_csv(decisions_csv)
    base = list(meta.cuts) if base_auto_cuts else []
    if keep_cuts:
        base = [c for c in base if not any(_overlaps(c, k) for k in keep_cuts)]
    final_cuts = [*base, *add_cuts]
    final_cuts.sort(key=lambda c: (c.start, c.end))
    out_path = out.expanduser() if out is not None else decisions_csv.with_name(f"{decisions_csv.stem}.decisions.edl")
    src = source_name or Path(meta.source_path).name or "source"
    output.write_edl(final_cuts, out_path, meta.duration, fps, source_name=src)
    console.print("[green]결정 적용 완료[/green]")
    console.print(f"  CSV 행: {rows}")
    console.print(f"  기존 자동 컷: {len(meta.cuts) if base_auto_cuts else 0}개")
    console.print(f"  추가 cut 결정: {len(add_cuts)}개")
    console.print(f"  keep 결정으로 제거: {len(keep_cuts)}개 후보")
    console.print(f"  최종 컷: {len(final_cuts)}개")
    console.print(f"  출력: {out_path}")

def main(args: Iterable[str] | None = None) -> None:
    """콘솔 스크립트/``python -m kocut`` 공용 진입점."""
    app(args=_normalise_args(sys.argv[1:] if args is None else args))


if __name__ == "__main__":
    main()
