"""KoCut 명령줄 인터페이스.

`python -m kocut process <video>` 로 전체 파이프라인을 실행합니다:
오디오 추출 → 한국어 트랜스크립션 → 자막 분할 → 컷 후보 검출 → 파일 출력.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from kocut import audio, fillers, output, retakes, shorts, silence, subtitles, transcribe
from kocut.audio import FFmpegError
from kocut.logger import get_logger
from kocut.transcribe import WhisperNotInstalledError
from kocut.types import Meta

app = typer.Typer(add_completion=False, help="KoCut — 한국어 영상 자동 편집 보조 도구")
console = Console()


def _run_pipeline(
    video: Path,
    out_dir: Path,
    log_path: Path,
    model: str,
    device: str,
    fps: float,
    skip_fillers: bool,
    skip_silence: bool,
    skip_retakes: bool,
    skip_shorts: bool,
    verbose: bool,
) -> None:
    logger = get_logger("kocut", log_file=log_path, verbose=verbose)

    # 1. 오디오 추출
    wav_path = out_dir / f"{video.stem}.kocut.wav"
    with console.status("[1/6] 오디오 추출 중..."):
        audio.extract_wav(video, wav_path)
        duration = audio.get_duration(video)
    logger.info("오디오 추출 완료 (%.1f초)", duration)

    try:
        # 2. 트랜스크립션
        console.print("[2/6] 한국어 트랜스크립션 중... (첫 실행은 모델 다운로드로 시간이 걸립니다)")
        segments = transcribe.transcribe_korean(str(wav_path), model_name=model, device=device)
        words = [w for seg in segments for w in seg.words]
        logger.info("트랜스크립션 완료: 세그먼트 %d개, 단어 %d개", len(segments), len(words))

        if not words:
            console.print("[yellow]경고: 음성을 인식하지 못했습니다. 영상에 한국어 음성이 있는지 확인하세요.[/yellow]")

        # 3. 자막 분할
        with console.status("[3/6] 자막 분할 중..."):
            subs = subtitles.split_subtitles(words)
        logger.info("자막 %d줄 생성", len(subs))

        # 4. 컷 후보 검출
        cuts = []
        with console.status("[4/6] 컷 후보 검출 중..."):
            if not skip_fillers:
                cuts += fillers.detect_fillers(words)
            if not skip_silence:
                cuts += silence.detect_silences(str(wav_path), words)
            if not skip_retakes:
                cuts += retakes.detect_retakes(segments)
        cuts.sort(key=lambda c: c.start)
        logger.info("컷 후보 %d개 검출", len(cuts))

        # 5. 쇼츠 후보
        with console.status("[5/6] 쇼츠 후보 점수 중..."):
            shorts_list = [] if skip_shorts else shorts.score_shorts_candidates(segments)
        logger.info("쇼츠 후보 %d개", len(shorts_list))

        # 6. 출력
        with console.status("[6/6] 결과 파일 작성 중..."):
            meta = Meta(
                source_path=str(video),
                duration=duration,
                model=model,
                segments=segments,
                subtitles=subs,
                cuts=cuts,
                shorts=shorts_list,
            )
            srt_path = output.write_srt(subs, out_dir / f"{video.stem}.srt")
            edl_path = output.write_edl(cuts, out_dir / f"{video.stem}.cuts.edl", duration, fps)
            json_path = output.write_meta_json(meta, out_dir / f"{video.stem}.meta.json")
    finally:
        # 임시 WAV는 성공/실패 무관하게 정리
        try:
            wav_path.unlink()
        except OSError:
            pass

    # 요약 표
    table = Table(title="처리 완료")
    table.add_column("항목")
    table.add_column("값", justify="right")
    table.add_row("영상 길이", f"{duration:.1f}초")
    table.add_row("자막", f"{len(subs)}줄")
    table.add_row("간투사 컷", str(sum(1 for c in cuts if c.kind == "filler")))
    table.add_row("무음 컷", str(sum(1 for c in cuts if c.kind == "silence")))
    table.add_row("재촬영 컷", str(sum(1 for c in cuts if c.kind == "retake")))
    table.add_row("쇼츠 후보", str(len(shorts_list)))
    console.print(table)

    console.print("\n[green]생성된 파일:[/green]")
    console.print(f"  자막:     {srt_path}")
    console.print(f"  컷 EDL:   {edl_path}")
    console.print(f"  메타데이터: {json_path}")
    console.print("\nPremiere/DaVinci에서 SRT는 자막으로, EDL은 시퀀스로 import 하세요.")


@app.command()
def process(
    video: Path = typer.Argument(..., help="처리할 영상 파일 경로"),
    output_dir: Path = typer.Option(None, "--output-dir", "-o", help="출력 폴더 (기본: 영상과 같은 폴더)"),
    model: str = typer.Option("large-v3", "--model", "-m", help="Whisper 모델 이름"),
    device: str = typer.Option("auto", "--device", "-d", help="처리 장치: auto / cuda / cpu (GPU 오류 시 cpu)"),
    fps: float = typer.Option(30.0, "--fps", help="EDL 프레임레이트"),
    skip_fillers: bool = typer.Option(False, "--skip-fillers", help="간투사 검출 건너뛰기"),
    skip_silence: bool = typer.Option(False, "--skip-silence", help="무음 검출 건너뛰기"),
    skip_retakes: bool = typer.Option(False, "--skip-retakes", help="재촬영 검출 건너뛰기"),
    skip_shorts: bool = typer.Option(False, "--skip-shorts", help="쇼츠 후보 건너뛰기"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="상세 로그"),
) -> None:
    """영상을 분석해 자막(SRT) + 컷 후보(EDL) + 메타데이터(JSON)를 생성합니다."""
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
            video, out_dir, log_path, model, device, fps,
            skip_fillers, skip_silence, skip_retakes, skip_shorts, verbose,
        )
    except FFmpegError as exc:
        console.print(f"\n[red]오디오 처리 실패:[/red] {exc}")
        raise typer.Exit(code=1)
    except WhisperNotInstalledError as exc:
        console.print(f"\n[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("\n[yellow]사용자가 중단했습니다.[/yellow]")
        raise typer.Exit(code=130)
    except Exception as exc:  # noqa: BLE001  (사용자에게 친절한 메시지를 위해 광범위 캐치)
        logger = get_logger("kocut", log_file=log_path, verbose=verbose)
        logger.exception("처리 중 예상치 못한 오류")
        console.print(f"\n[red]예상치 못한 오류:[/red] {exc}")
        console.print(f"[dim]자세한 내용은 로그를 확인하세요: {log_path}[/dim]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
