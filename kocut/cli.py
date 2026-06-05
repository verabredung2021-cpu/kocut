"""KoCut 명령줄 인터페이스.

다음 두 형식을 모두 지원합니다:
- 새/간단 형식: ``python -m kocut video.mp4``
- 하위 명령 형식: ``python -m kocut process video.mp4``

전체 파이프라인은 오디오 추출 → 한국어 트랜스크립션 → 자막 분할 → 컷 후보
검출 → 파일 출력 순서로 실행됩니다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from kocut import pipeline
from kocut.audio import FFmpegError
from kocut.logger import get_logger
from kocut.pipeline import FILLER_MODES
from kocut.transcribe import WhisperNotInstalledError
from kocut.types import Segment, Word

app = typer.Typer(
    add_completion=False,
    help="KoCut — 한국어 영상 자동 편집 보조 도구",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

_COMMANDS = {"process"}
_ROOT_HELP_OPTIONS = {"-h", "--help", "--install-completion", "--show-completion"}
_VALID_DEVICES = {"auto", "cuda", "cpu"}
# faster-whisper/ctranslate2에서 널리 쓰이는 compute_type 값.
# 새 ctranslate2 값이 추가될 수 있으므로 CLI에서는 경고 없이 막기보다 대표 오타만 잡습니다.
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
    """기존/신규 실행 형식을 모두 받도록 argv를 정규화합니다.

    Typer는 명령이 하나뿐이면 ``process``를 생략한 단일 명령 형태로 접는 경향이
    있습니다. 사용자는 이미 ``python -m kocut process ...`` 형식도 쓰고 있으므로,
    실제 진입점에서는 ``process``가 없을 때만 자동으로 붙여 줍니다.
    """
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
    pad_before_ms: int = 0,
    pad_after_ms: int = 0,
    min_cut_ms: int = 100,
    min_clip_ms: int = 100,
    min_silence_ms: int = 600,
    filler_mode: str = "balanced",
    skip_fcpxml: bool = False,
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
            pad_before_ms=pad_before_ms,
            pad_after_ms=pad_after_ms,
            min_cut_ms=min_cut_ms,
            min_clip_ms=min_clip_ms,
            min_silence_ms=min_silence_ms,
            filler_mode=filler_mode,
            on_progress=on_progress,
            logger=logger,
        )

    meta = result.meta
    if result.used_word_fallback:
        console.print("[yellow]경고: word timestamp가 없어 세그먼트 단위 자막으로 보강했습니다.[/yellow]")
    if not meta.subtitles and not meta.segments:
        console.print("[yellow]경고: 음성을 인식하지 못했습니다. 영상에 한국어 음성이 있는지 확인하세요.[/yellow]")

    table = Table(title="처리 완료")
    table.add_column("항목")
    table.add_column("값", justify="right")
    table.add_row("영상 길이", f"{meta.duration:.1f}초")
    table.add_row("프레임레이트", f"{result.fps:g}fps")
    table.add_row("자막", f"{len(meta.subtitles)}줄")
    table.add_row("간투사 컷", str(sum(1 for c in meta.cuts if c.kind == "filler")))
    table.add_row("무음 컷", str(sum(1 for c in meta.cuts if c.kind == "silence")))
    table.add_row("재촬영 컷", str(sum(1 for c in meta.cuts if c.kind == "retake")))
    if meta.filler_candidates:
        table.add_row("검토 후보(애매 간투사)", str(len(meta.filler_candidates)))
    table.add_row("쇼츠 후보", str(len(meta.shorts)))
    console.print(table)

    console.print("\n[green]생성된 파일:[/green]")
    console.print(f"  자막:        {result.srt_path}")
    console.print(f"  컷 EDL:      {result.edl_path}")
    if result.fcpxml_path is not None:
        console.print(f"  컷 FCPXML:   {result.fcpxml_path}  [dim](beta)[/dim]")
    console.print(f"  미리보기:    {result.review_path}")
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
    filler_mode: str = typer.Option(
        "balanced", "--filler-mode",
        help="간투사 적용 강도: conservative(핵심만) / balanced(기본) / aggressive(애매한 것까지)",
    ),
    skip_fillers: bool = typer.Option(False, "--skip-fillers", help="간투사 검출 건너뛰기"),
    skip_silence: bool = typer.Option(False, "--skip-silence", help="무음 검출 건너뛰기"),
    skip_retakes: bool = typer.Option(False, "--skip-retakes", help="재촬영 검출 건너뛰기"),
    skip_shorts: bool = typer.Option(False, "--skip-shorts", help="쇼츠 후보 건너뛰기"),
    keep_wav: bool = typer.Option(False, "--keep-wav", help="Whisper 입력용 WAV를 삭제하지 않고 보관"),
    pad_before_ms: int = typer.Option(0, "--pad-before-ms", help="다음 발화 시작 전 남길 여유(ms) — 말 앞부분 보호"),
    pad_after_ms: int = typer.Option(0, "--pad-after-ms", help="직전 발화 끝난 뒤 남길 여유(ms) — 말 뒷부분 보호"),
    min_silence_ms: int = typer.Option(600, "--min-silence-ms", help="이보다 긴 무음만 컷 (작을수록 더 잘게 자름, 기본 600)"),
    min_cut_ms: int = typer.Option(100, "--min-cut-ms", help="이보다 짧은 컷은 무시(ms) — 1~2프레임짜리 자잘한 컷 방지"),
    min_clip_ms: int = typer.Option(100, "--min-clip-ms", help="이보다 짧은 '남길 구간'은 제거(ms) — 마이크로 클립 방지"),
    skip_fcpxml: bool = typer.Option(False, "--skip-fcpxml", help="FCPXML 출력 건너뛰기"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="상세 로그"),
) -> None:
    """영상을 분석해 자막(SRT) + 컷 후보(EDL/FCPXML) + 메타데이터(JSON)를 생성합니다."""
    if filler_mode not in FILLER_MODES:
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
            skip_fillers, skip_silence, skip_retakes, skip_shorts, keep_wav, verbose,
            pad_before_ms=pad_before_ms,
            pad_after_ms=pad_after_ms,
            min_cut_ms=min_cut_ms,
            min_clip_ms=min_clip_ms,
            min_silence_ms=min_silence_ms,
            filler_mode=filler_mode,
            skip_fcpxml=skip_fcpxml,
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


def main(args: Iterable[str] | None = None) -> None:
    """콘솔 스크립트/``python -m kocut`` 공용 진입점."""
    app(args=_normalise_args(sys.argv[1:] if args is None else args))


if __name__ == "__main__":
    main()
