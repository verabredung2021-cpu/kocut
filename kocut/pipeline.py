"""KoCut 분석 파이프라인 (CLI·GUI 공용).

이전에는 cli.py와 gui.py가 각자 파이프라인을 복제하고 있어서, 한쪽에만 개선이
들어가면(예: 단어 경계 정제) 다른 쪽은 옛 동작으로 남는 문제가 있었습니다. 이
모듈의 `analyze()`를 양쪽에서 호출해 결과가 항상 같도록 통일합니다.

진행 상황은 `on_progress(fraction, description)` 콜백으로 보고합니다. CLI는 이를
rich 진행 표시줄에, GUI는 gradio 진행바에 연결합니다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kocut import (
    audio,
    fcpxml,
    fillers,
    output,
    refine,
    retakes,
    review,
    shorts,
    silence,
    subtitles,
    transcribe,
)
from kocut.types import CutCandidate, Meta, Segment, Word

ProgressFn = Callable[[float, str], None]

# --filler-mode → 자동 컷 confidence 임계값. 핵심 간투사=0.9, 애매=0.6.
FILLER_MODES = ("conservative", "balanced", "aggressive")
_FILLER_MODE_THRESHOLD = {"conservative": 0.85, "balanced": 0.5, "aggressive": 0.0}


@dataclass
class PipelineResult:
    """analyze() 결과 — 메타데이터와 생성된 파일 경로."""

    meta: Meta
    media: audio.MediaInfo
    fps: float
    srt_path: Path
    edl_path: Path
    json_path: Path
    review_path: Path
    fcpxml_path: Path | None
    wav_path: Path | None
    used_word_fallback: bool


def words_from_segments(segments: list[Segment]) -> tuple[list[Word], bool]:
    """word timestamp가 비어 있으면 세그먼트 단위 자막으로 보강합니다."""
    words: list[Word] = []
    used_fallback = False
    for seg in segments:
        if seg.words:
            words.extend(seg.words)
            continue
        text = seg.text.strip()
        if text:
            used_fallback = True
            words.append(Word(word=text, start=seg.start, end=seg.end, prob=None))
    return words, used_fallback


def _split_fillers(
    cuts: list[CutCandidate], filler_mode: str
) -> tuple[list[CutCandidate], list[CutCandidate]]:
    """간투사를 모드 임계값 기준으로 (자동 컷, 검토 후보)로 나눕니다.

    간투사가 아닌 컷(무음·재촬영)은 항상 자동 컷에 포함합니다.
    """
    threshold = _FILLER_MODE_THRESHOLD.get(filler_mode, 0.5)
    keep: list[CutCandidate] = []
    candidates: list[CutCandidate] = []
    for c in cuts:
        if c.kind == "filler" and c.confidence < threshold:
            candidates.append(c)
        else:
            keep.append(c)
    return keep, candidates


def analyze(
    video: Path,
    out_dir: Path,
    *,
    model: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    fps: float | None = None,
    skip_fillers: bool = False,
    skip_silence: bool = False,
    skip_retakes: bool = False,
    skip_shorts: bool = False,
    skip_fcpxml: bool = False,
    keep_wav: bool = False,
    pad_before_ms: int = 0,
    pad_after_ms: int = 0,
    min_cut_ms: int = 0,
    min_clip_ms: int = 100,
    filler_mode: str = "balanced",
    on_progress: ProgressFn | None = None,
    logger: logging.Logger | None = None,
) -> PipelineResult:
    """영상을 분석해 자막·컷(EDL/FCPXML)·메타·미리보기 리포트를 생성합니다.

    fps가 None이면 ffprobe로 읽은 원본 fps를 사용합니다(없으면 30). 해상도와 시작
    타임코드도 ffprobe로 읽어 FCPXML 해상도와 EDL relink 보정에 씁니다.
    """
    log = logger or logging.getLogger("kocut.pipeline")

    def progress(fraction: float, desc: str) -> None:
        if on_progress is not None:
            on_progress(max(0.0, min(1.0, fraction)), desc)

    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{video.stem}.kocut.wav"
    kept_wav: Path | None = None

    try:
        # 0. 미디어 프로브
        progress(0.03, "미디어 정보 읽는 중...")
        media = audio.probe_media(video)
        effective_fps = fps if fps is not None else media.fps
        if not (effective_fps and effective_fps > 0):
            effective_fps = 30.0
        log.info(
            "미디어: %.1f초, %gfps, %dx%d, 시작TC=%s",
            media.duration, effective_fps, media.width, media.height, media.start_tc or "없음",
        )

        # 1. 오디오 추출
        progress(0.06, "오디오 추출 중...")
        audio.extract_wav(video, wav_path)
        duration = media.duration if media.duration > 0 else audio.get_duration(video)

        # 2. 트랜스크립션 (세그먼트 단위 진행률)
        progress(0.12, "한국어 트랜스크립션 중... (첫 실행은 모델 다운로드)")
        segments: list[Segment] = []
        for seg in transcribe.iter_segments(
            str(wav_path), model_name=model, device=device, compute_type=compute_type
        ):
            segments.append(seg)
            if duration > 0:
                progress(0.12 + 0.55 * min(seg.end / duration, 1.0), "한국어 트랜스크립션 중...")
        words, used_word_fallback = words_from_segments(segments)
        log.info("트랜스크립션 완료: 세그먼트 %d, 단어 %d", len(segments), len(words))

        warnings: list[str] = []
        if used_word_fallback:
            warnings.append("word timestamp가 없어 세그먼트 단위로 자막을 생성했습니다.")
            log.warning(warnings[-1])
        if not words:
            warnings.append("음성을 인식하지 못했습니다. 한국어 음성이 있는지 확인하세요.")

        # 3. 자막 분할
        progress(0.7, "자막 분할 중...")
        subs = subtitles.split_subtitles(words)

        # 4. 컷 후보 검출 → 간투사 모드 분리 → 단어 경계 정제
        progress(0.78, "컷 후보 검출 중...")
        raw_cuts: list[CutCandidate] = []
        if not skip_fillers:
            raw_cuts += fillers.detect_fillers(words)
        if not skip_silence:
            raw_cuts += silence.detect_silences(str(wav_path), words)
        if not skip_retakes:
            raw_cuts += retakes.detect_retakes(segments)
        to_cut, filler_candidates = _split_fillers(raw_cuts, filler_mode)
        cuts = refine.refine_cuts(
            to_cut,
            words,
            pad_before=max(0, pad_before_ms) / 1000.0,
            pad_after=max(0, pad_after_ms) / 1000.0,
            min_cut=max(0, min_cut_ms) / 1000.0,
        )
        cuts.sort(key=lambda c: c.start)
        filler_candidates.sort(key=lambda c: c.start)
        log.info(
            "컷 후보 %d개 → 모드[%s]·정제 후 %d개 (검토 후보 %d개)",
            len(raw_cuts), filler_mode, len(cuts), len(filler_candidates),
        )

        # 5. 쇼츠 후보
        progress(0.88, "쇼츠 후보 점수 중...")
        shorts_list = [] if skip_shorts else shorts.score_shorts_candidates(segments)

        # 6. 출력
        progress(0.95, "결과 파일 작성 중...")
        meta = Meta(
            source_path=str(video),
            duration=duration,
            model=model,
            segments=segments,
            subtitles=subs,
            cuts=cuts,
            filler_candidates=filler_candidates,
            shorts=shorts_list,
            warnings=warnings,
        )
        min_clip_seconds = max(0, min_clip_ms) / 1000.0
        srt_path = output.write_srt(subs, out_dir / f"{video.stem}.srt")
        edl_path = output.write_edl(
            cuts,
            out_dir / f"{video.stem}.cuts.edl",
            duration,
            effective_fps,
            source_name=video.name,
            min_clip_seconds=min_clip_seconds,
            source_start_tc=media.start_tc,
        )
        fcpxml_path: Path | None = None
        if not skip_fcpxml:
            fcpxml_path = fcpxml.write_fcpxml(
                cuts,
                out_dir / f"{video.stem}.fcpxml",
                duration,
                effective_fps,
                source_path=str(video),
                width=media.width,
                height=media.height,
                min_clip_seconds=min_clip_seconds,
            )
        json_path = output.write_meta_json(meta, out_dir / f"{video.stem}.meta.json")
        review_path = review.write_review(
            meta, out_dir / f"{video.stem}.cuts.md", fps=effective_fps
        )
        progress(1.0, "완료")
    finally:
        if keep_wav and wav_path.exists():
            kept_wav = wav_path
        elif wav_path.exists():
            try:
                wav_path.unlink()
            except OSError:
                pass

    return PipelineResult(
        meta=meta,
        media=media,
        fps=effective_fps,
        srt_path=srt_path,
        edl_path=edl_path,
        json_path=json_path,
        review_path=review_path,
        fcpxml_path=fcpxml_path,
        wav_path=kept_wav,
        used_word_fallback=used_word_fallback,
    )
