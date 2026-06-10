"""KoCut 분석 파이프라인 (CLI·GUI 공용).

CLI와 GUI가 동일한 `analyze()`를 호출합니다. v0.7부터는 무음 후보를 전부
반영하지 않고, 단어 gap + 컷 예산 + 프리셋별 audition EDL을 생성하는 구조로
변경했습니다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kocut import (
    audio,
    director,
    fcpxml,
    fillers,
    output,
    quality,
    refine,
    retakes,
    review,
    shorts,
    silence,
    subtitles,
    transcribe,
    xmeml,
)
from kocut.types import CutCandidate, Meta, Segment, Utterance, Word

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
    paper_edit_path: Path
    review_candidates_path: Path
    html_review_path: Path
    fcpxml_path: Path | None
    xmeml_path: Path | None
    wav_path: Path | None
    used_word_fallback: bool
    variant_edl_paths: dict[str, Path] = field(default_factory=dict)
    variants_report_path: Path | None = None


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


def _plan_cuts(
    *,
    words: list[Word],
    segments: list[Segment],
    utterances: list[Utterance] | None,
    wav_path: Path,
    duration: float,
    preset_name: str,
    skip_silence: bool,
    filler_cuts: list[CutCandidate],
    retake_cuts: list[CutCandidate],
    pad_before_ms: int | None = None,
    pad_after_ms: int | None = None,
    min_cut_ms: int | None = None,
    min_silence_ms: int | None = None,
    min_keep_between_cuts_ms: int | None = None,
    filler_mode: str | None = None,
    director_mode: bool = True,
) -> tuple[list[CutCandidate], list[CutCandidate], int]:
    """프리셋 하나에 대해 최종 컷과 검토 후보를 계산합니다."""
    preset = quality.get_preset(preset_name)
    effective_min_silence_ms = preset.min_silence_ms if min_silence_ms is None else min_silence_ms
    effective_min_cut_ms = preset.min_cut_ms if min_cut_ms is None else min_cut_ms
    effective_pad_before_ms = preset.pad_before_ms if pad_before_ms is None else pad_before_ms
    effective_pad_after_ms = preset.pad_after_ms if pad_after_ms is None else pad_after_ms
    effective_min_keep_ms = (
        preset.min_keep_between_cuts_ms
        if min_keep_between_cuts_ms is None
        else min_keep_between_cuts_ms
    )
    effective_filler_mode = preset.filler_mode if filler_mode is None else filler_mode

    silence_cuts: list[CutCandidate] = []
    if not skip_silence:
        if director_mode and utterances and preset.name != "_legacy":
            # v0.8 핵심: 단어 사이 gap이 아니라 문장/호흡 단위 사이 gap만 자릅니다.
            silence_cuts = director.sentence_boundary_silence_cuts(
                utterances,
                duration,
                preset=preset,
                min_silence_ms=max(0, effective_min_silence_ms),
                pad_before_ms=max(0, effective_pad_before_ms),
                pad_after_ms=max(0, effective_pad_after_ms),
                min_cut_ms=max(0, effective_min_cut_ms),
            )
        elif words and preset.name != "_legacy":
            # v0.7 방식: word gap + 발화 리듬 + 컷 예산.
            silence_cuts = quality.contextual_silence_cuts(
                words,
                duration,
                preset=preset,
                min_silence_ms=max(0, effective_min_silence_ms),
                pad_before_ms=max(0, effective_pad_before_ms),
                pad_after_ms=max(0, effective_pad_after_ms),
                min_cut_ms=max(0, effective_min_cut_ms),
            )
        else:
            silence_cuts = silence.detect_silences(
                str(wav_path),
                words,
                min_ms=max(0, effective_min_silence_ms),
                padding_ms=max(effective_pad_before_ms, effective_pad_after_ms, 0),
                min_cut_ms=max(0, effective_min_cut_ms),
            )

    raw_cuts = [*filler_cuts, *silence_cuts, *retake_cuts]
    to_cut, filler_candidates = _split_fillers(raw_cuts, effective_filler_mode)
    cuts = refine.refine_cuts(
        to_cut,
        words,
        pad_before=max(0, effective_pad_before_ms) / 1000.0,
        pad_after=max(0, effective_pad_after_ms) / 1000.0,
        min_cut=0.0,
    )
    cuts = quality.smooth_cuts(
        cuts,
        duration,
        min_cut_seconds=max(0, effective_min_cut_ms) / 1000.0,
        min_keep_between_cuts_seconds=max(0, effective_min_keep_ms) / 1000.0,
        max_cuts_per_minute=preset.max_cuts_per_minute,
        max_remove_ratio=preset.max_remove_ratio,
    )
    cuts.sort(key=lambda c: c.start)
    filler_candidates.sort(key=lambda c: c.start)
    return cuts, filler_candidates, len(raw_cuts)


def _write_variants_report(
    path: Path,
    *,
    duration: float,
    main_preset: str,
    variant_paths: dict[str, Path],
    variant_cuts: dict[str, list[CutCandidate]],
) -> Path:
    lines = [
        "# KoCut cut variants",
        "",
        "한 번의 Whisper 분석으로 만든 프리셋별 EDL입니다. Premiere/DaVinci에 각각 넣어보고 가장 자연스러운 것을 고르세요.",
        "",
        f"- 메인 프리셋: `{main_preset}`",
        f"- 원본 길이: {duration:.1f}초",
        "",
        "| 프리셋 | 컷 수 | 삭제 합계 | 예상 결과 길이 | 파일 |",
        "|---|---:|---:|---:|---|",
    ]
    for name in quality.preset_pack_names():
        if name not in variant_cuts:
            continue
        cuts = variant_cuts[name]
        removed = sum(c.duration for c in cuts)
        file_name = variant_paths.get(name, Path("-"))
        lines.append(
            f"| `{name}` | {len(cuts)} | {removed:.1f}s | {max(0.0, duration - removed):.1f}s | `{file_name.name}` |"
        )
    lines += [
        "",
        "## 추천",
        "",
        "- 상담/강의/롱폼은 `safe` 또는 `balanced`부터 확인하세요.",
        "- 컷백처럼 빠른 템포가 필요하면 `cutback`을 보세요.",
        "- `aggressive`는 쇼츠/짧은 영상용입니다. 원장님 상담 영상에는 기본으로 권장하지 않습니다.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


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
    skip_xml: bool = False,
    keep_wav: bool = False,
    cut_preset: str = "safe",
    pad_before_ms: int | None = None,
    pad_after_ms: int | None = None,
    min_cut_ms: int | None = None,
    min_clip_ms: int | None = None,
    min_silence_ms: int | None = None,
    min_keep_between_cuts_ms: int | None = None,
    filler_mode: str | None = None,
    write_variants: bool = True,
    director_mode: bool = True,
    on_progress: ProgressFn | None = None,
    logger: logging.Logger | None = None,
) -> PipelineResult:
    """영상을 분석해 자막·컷(EDL/FCPXML)·메타·미리보기 리포트를 생성합니다.

    fps가 None이면 ffprobe로 읽은 원본 fps를 사용합니다(없으면 30). 해상도와 시작
    타임코드도 ffprobe로 읽어 FCPXML 해상도와 EDL relink 보정에 씁니다.
    """
    log = logger or logging.getLogger("kocut.pipeline")
    preset = quality.get_preset(cut_preset)

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

        # 3-b. 문장/호흡 단위 paper edit
        progress(0.73, "문장 단위 분석 중...")
        utterances = director.build_utterances(words)
        topic_sections = director.build_topic_sections(utterances)
        production_cuts = director.detect_production_cuts(utterances)
        review_candidates = director.detect_review_candidates(utterances, words)

        # 4. 컷 후보 검출 → 프리셋별 플래너 → 단어 경계 정제
        progress(0.78, "컷 후보 검출 중...")
        filler_raw = [] if skip_fillers else fillers.detect_fillers(words)
        retake_raw = [] if skip_retakes else retakes.detect_retakes(segments)
        retake_plus_production = [*production_cuts, *retake_raw]
        cuts, filler_candidates, raw_count = _plan_cuts(
            words=words,
            segments=segments,
            utterances=utterances,
            wav_path=wav_path,
            duration=duration,
            preset_name=preset.name,
            skip_silence=skip_silence,
            filler_cuts=filler_raw,
            retake_cuts=retake_plus_production,
            pad_before_ms=pad_before_ms,
            pad_after_ms=pad_after_ms,
            min_cut_ms=min_cut_ms,
            min_silence_ms=min_silence_ms,
            min_keep_between_cuts_ms=min_keep_between_cuts_ms,
            filler_mode=filler_mode,
            director_mode=director_mode,
        )
        log.info(
            "컷 후보 %d개 → 프리셋[%s]·정제 후 %d개 (검토 후보 %d개)",
            raw_count, preset.name, len(cuts), len(filler_candidates),
        )

        # 4-b. 프리셋별 audition EDL 생성용 컷 계산 (Whisper 재실행 없음)
        variant_cuts: dict[str, list[CutCandidate]] = {}
        variant_candidates: dict[str, list[CutCandidate]] = {}
        if write_variants:
            for name in quality.preset_pack_names():
                vcuts, vcands, _raw = _plan_cuts(
                    words=words,
                    segments=segments,
                    utterances=utterances,
                    wav_path=wav_path,
                    duration=duration,
                    preset_name=name,
                    skip_silence=skip_silence,
                    filler_cuts=filler_raw,
                    retake_cuts=retake_plus_production,
                    filler_mode=None,
                    director_mode=director_mode,
                )
                variant_cuts[name] = vcuts
                variant_candidates[name] = vcands
        else:
            variant_cuts[preset.name] = cuts

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
            utterances=utterances,
            topic_sections=topic_sections,
            cuts=cuts,
            filler_candidates=filler_candidates,
            review_candidates=review_candidates,
            shorts=shorts_list,
            warnings=warnings,
        )
        min_clip_seconds = max(0, min_clip_ms or 0) / 1000.0
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
        xmeml_path: Path | None = None
        if not skip_xml:
            xmeml_path = xmeml.write_xmeml(
                cuts,
                out_dir / f"{video.stem}.premiere.xml",
                duration,
                effective_fps,
                source_path=str(video),
                width=media.width,
                height=media.height,
                min_clip_seconds=min_clip_seconds,
            )
        variant_edl_paths: dict[str, Path] = {}
        if write_variants:
            for name, vcuts in variant_cuts.items():
                path = output.write_edl(
                    vcuts,
                    out_dir / f"{video.stem}.cuts.{name}.edl",
                    duration,
                    effective_fps,
                    source_name=video.name,
                    min_clip_seconds=min_clip_seconds,
                    source_start_tc=media.start_tc,
                )
                variant_edl_paths[name] = path
        variants_report_path = None
        if variant_edl_paths:
            variants_report_path = review.write_variants_review(
                video.name,
                out_dir / f"{video.stem}.cut_variants.md",
                duration=duration,
                variants=variant_cuts,
            )
        json_path = output.write_meta_json(meta, out_dir / f"{video.stem}.meta.json")
        review_path = review.write_review(
            meta, out_dir / f"{video.stem}.cuts.md", fps=effective_fps
        )
        paper_edit_path = director.write_paper_edit_csv(utterances, out_dir / f"{video.stem}.paper_edit.csv")
        review_candidates_path = director.write_review_decisions_csv(
            [*filler_candidates, *review_candidates], out_dir / f"{video.stem}.review_decisions.csv", utterances
        )
        html_review_path = director.write_director_html(
            source_name=video.name,
            out_path=out_dir / f"{video.stem}.director_review.html",
            duration=duration,
            utterances=utterances,
            cuts=cuts,
            review_candidates=[*filler_candidates, *review_candidates],
            topics=topic_sections,
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
        paper_edit_path=paper_edit_path,
        review_candidates_path=review_candidates_path,
        html_review_path=html_review_path,
        fcpxml_path=fcpxml_path,
        xmeml_path=xmeml_path,
        wav_path=kept_wav,
        used_word_fallback=used_word_fallback,
        variant_edl_paths=variant_edl_paths,
        variants_report_path=variants_report_path,
    )
