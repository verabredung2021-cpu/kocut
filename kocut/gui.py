"""KoCut GUI — Gradio 기반 로컬 웹 인터페이스.

브라우저에서 열리는 간단한 GUI입니다. 영상 경로를 입력하거나 파일을 선택하면
자막·컷 후보·쇼츠 후보를 분석해 표로 보여주고, SRT/EDL/FCPXML/리포트/JSON을
내려받을 수 있습니다. CLI와 **동일한** `pipeline.analyze()`를 호출하므로 결과가
항상 같습니다(단어 경계 정제·FCPXML·미리보기 포함).

실행:
    python -m kocut.gui
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

import gradio as gr

from kocut import pipeline
from kocut.audio import FFmpegError
from kocut.transcribe import WhisperNotInstalledError

_KIND_KR = {"filler": "간투사", "silence": "무음", "retake": "재촬영", "low_info": "저정보"}

GuiResult = tuple[
    str,
    list[list[object]],
    list[list[object]],
    list[list[object]],
    list[list[object]],
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
]


def _fmt_time(seconds: float) -> str:
    """초를 M:SS.s 형식으로."""
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:04.1f}"


def _resolve_path(path_text: str, uploaded: str | None) -> Path:
    """경로 입력과 업로드 파일 중 유효한 것을 고릅니다 (경로 입력 우선)."""
    candidate = (path_text or "").strip().strip('"')
    if candidate:
        return Path(candidate).expanduser()
    if uploaded:
        return Path(uploaded)
    raise gr.Error("영상 경로를 입력하거나 파일을 선택하세요.")


def process_video(
    path_text: str,
    uploaded: str | None,
    model: str,
    device: str,
    compute_type: str,
    do_fillers: bool,
    do_silence: bool,
    do_retakes: bool,
    do_shorts: bool,
    fps: float | None,
    cut_preset: str,
    filler_mode: str,
    director_mode: bool,
    progress: gr.Progress = gr.Progress(),  # noqa: B008
) -> GuiResult:
    """영상을 분석해 (요약, 자막표, 컷표, 검토후보표, 쇼츠표, srt, edl, fcpxml, 리포트, json)을 반환합니다."""
    video = _resolve_path(path_text, uploaded)
    if not video.exists():
        raise gr.Error(f"파일을 찾을 수 없습니다: {video}")

    out_dir = Path(tempfile.mkdtemp(prefix="kocut_"))

    try:
        result = pipeline.analyze(
            video,
            out_dir,
            model=model,
            device=device,
            compute_type=compute_type,
            fps=fps,
            skip_fillers=not do_fillers,
            skip_silence=not do_silence,
            skip_retakes=not do_retakes,
            skip_shorts=not do_shorts,
            cut_preset=cut_preset,
            filler_mode=filler_mode,
            director_mode=director_mode,
            on_progress=lambda f, d: progress(f, desc=d),
        )
    except FFmpegError as exc:
        raise gr.Error(f"오디오 처리 실패: {exc}")
    except WhisperNotInstalledError as exc:
        raise gr.Error(str(exc))
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"처리 중 오류: {exc}")

    meta = result.meta
    cuts = meta.cuts
    n_fill = sum(1 for c in cuts if c.kind == "filler")
    n_sil = sum(1 for c in cuts if c.kind == "silence")
    n_ret = sum(1 for c in cuts if c.kind == "retake")
    removed = sum(c.duration for c in cuts)
    final = max(0.0, meta.duration - removed)
    fallback_warning = (
        "- ⚠️ word timestamp가 없어 세그먼트 단위 자막으로 보강했습니다.\n"
        if result.used_word_fallback
        else ""
    )
    cand_note = (
        f"- 검토 필요(애매 간투사): **{len(meta.filler_candidates)}개** (아래 탭 참고)\n"
        if meta.filler_candidates
        else ""
    )
    summary = (
        f"### ✅ 분석 완료\n"
        f"- 영상 길이: **{meta.duration:.1f}초** ({meta.duration / 60:.1f}분) · {result.fps:g}fps\n"
        f"- 품질 프리셋: **{cut_preset}**\n"
        f"- 제거 예정: **{removed:.1f}초** → 결과 **{final:.1f}초**\n"
        f"- 자막: **{len(meta.subtitles)}줄**\n"
        f"- 컷 후보: **{len(cuts)}개** (간투사 {n_fill} · 무음 {n_sil} · 재촬영 {n_ret})\n"
        f"{cand_note}"
        f"- 문장 단위: **{len(meta.utterances)}개** · 리뷰 후보: **{len(meta.review_candidates)}개**\n"
        f"- 쇼츠 후보: **{len(meta.shorts)}개**\n"
        f"{fallback_warning}"
        f"\n아래 탭에서 결과를 확인하고, EDL/FCPXML을 내려받아 Premiere·DaVinci에 import 하세요. "
        f"relink가 어긋나면 FCPXML을 먼저 시도하세요."
    )

    sub_rows: list[list[object]] = [
        [s.index, _fmt_time(s.start), _fmt_time(s.end), s.text] for s in meta.subtitles
    ]
    cut_rows: list[list[object]] = [
        [_fmt_time(c.start), _fmt_time(c.end), f"{c.duration:.2f}s", _KIND_KR.get(c.kind, c.kind), c.text.strip() or c.reason]
        for c in cuts
    ]
    review_all = [*meta.filler_candidates, *meta.review_candidates]
    cand_rows: list[list[object]] = [
        [_fmt_time(c.start), _fmt_time(c.end), f"{c.duration:.2f}s", _KIND_KR.get(c.kind, c.kind), c.reason, c.text.strip() or c.reason]
        for c in review_all
    ]
    short_rows: list[list[object]] = [
        [_fmt_time(c.start), _fmt_time(c.end), round(c.score, 1), c.reason, c.text]
        for c in meta.shorts
    ]

    return (
        summary,
        sub_rows,
        cut_rows,
        cand_rows,
        short_rows,
        str(result.srt_path),
        str(result.edl_path),
        str(result.fcpxml_path) if result.fcpxml_path else "",
        str(result.review_path),
        str(result.json_path),
        str(result.paper_edit_path),
        str(result.review_candidates_path),
        str(result.html_review_path),
    )


def build_ui() -> gr.Blocks:
    """GUI를 구성해 Blocks를 반환합니다."""
    with gr.Blocks(title="KoCut") as demo:
        gr.Markdown(
            "# 🎬 KoCut — 한국어 영상 자동 편집\n"
            "영상을 분석해 **자막**과 **컷 후보**(무음·간투사·재촬영), **쇼츠 후보**를 자동 추출합니다. "
            "원본 영상은 수정하지 않으며, 결과는 표준 SRT/EDL/FCPXML 파일로 내보냅니다."
        )

        with gr.Row():
            with gr.Column(scale=2):
                path_text = gr.Textbox(
                    label="영상 경로",
                    placeholder=r"예: C:\Users\PD2106\Videos\강의.mp4   (큰 영상은 경로 입력 권장)",
                )
                uploaded = gr.File(label="또는 파일 선택 (작은 영상)", type="filepath")
            with gr.Column(scale=1):
                model = gr.Dropdown(
                    ["large-v3", "large-v3-turbo", "medium", "small"],
                    value="large-v3", label="Whisper 모델",
                    info="turbo는 빠르고 large-v3는 정확합니다",
                )
                device = gr.Dropdown(
                    [("자동 (GPU 우선, 실패 시 CPU)", "auto"), ("GPU (CUDA)", "cuda"), ("CPU", "cpu")],
                    value="auto", label="처리 장치",
                    info="GPU 오류가 나면 CPU를 선택하세요",
                )
                compute_type = gr.Dropdown(
                    ["auto", "float16", "int8", "int8_float16", "float32"],
                    value="auto", label="Compute type",
                    info="보통 auto 권장. CPU는 int8, CUDA는 float16이 일반적입니다",
                )
                fps = gr.Dropdown(
                    [("자동 (원본 감지)", None), (30.0, 30.0), (29.97, 29.97), (24.0, 24.0), (23.976, 23.976), (60.0, 60.0)],
                    value=None, label="프레임레이트 (EDL/FCPXML)",
                    info="미지정 시 원본에서 자동 감지",
                )
                cut_preset = gr.Dropdown(
                    [("안전 rough cut", "safe"), ("균형", "balanced"), ("컷백식 빠른 템포", "cutback"), ("공격적", "aggressive")],
                    value="safe", label="컷 품질 프리셋",
                    info="품질이 우선이면 safe부터 보세요",
                )
                filler_mode = gr.Dropdown(
                    [("보수적 (핵심 간투사만)", "conservative"), ("균형", "balanced"), ("적극적 (애매한 것까지)", "aggressive")],
                    value="conservative", label="간투사 적용 강도",
                    info="보수적이면 애매한 간투사는 '검토 후보'로만 표시",
                )

        with gr.Row():
            do_fillers = gr.Checkbox(value=True, label="간투사 검출 (어/음/그)")
            do_silence = gr.Checkbox(value=True, label="무음 검출")
            do_retakes = gr.Checkbox(value=False, label="재촬영 검출 (기본 꺼짐)")
            do_shorts = gr.Checkbox(value=True, label="쇼츠 후보")
            director_mode = gr.Checkbox(value=True, label="v0.8 문장 단위 컷 엔진")

        analyze_btn = gr.Button("🚀 분석 시작", variant="primary", size="lg")
        summary = gr.Markdown()

        with gr.Tabs():
            with gr.Tab("📝 자막"):
                sub_df = gr.Dataframe(
                    headers=["번호", "시작", "끝", "자막"],
                    datatype=["number", "str", "str", "str"],
                    wrap=True, label=None,
                )
            with gr.Tab("✂️ 컷 후보"):
                gr.Markdown("검토 후 직접 적용하세요. EDL/FCPXML은 이 컷들을 **제외한** 구간만 이어 붙입니다. 컷은 단어 경계로 보정되어 말이 씹히지 않습니다.")
                cut_df = gr.Dataframe(
                    headers=["시작", "끝", "길이", "종류", "내용/이유"],
                    datatype=["str", "str", "str", "str", "str"],
                    wrap=True,
                )
            with gr.Tab("🔎 검토 후보 (애매 간투사)"):
                gr.Markdown("문맥상 의미가 있을 수 있어 **자동 컷에서 제외**한 간투사입니다. 직접 확인 후 필요하면 적용하세요. (간투사 강도를 '적극적'으로 바꾸면 자동 컷에 포함됩니다.)")
                cand_df = gr.Dataframe(
                    headers=["시작", "끝", "길이", "종류", "이유", "텍스트"],
                    datatype=["str", "str", "str", "str", "str", "str"],
                    wrap=True,
                )
            with gr.Tab("🎞️ 쇼츠 후보"):
                short_df = gr.Dataframe(
                    headers=["시작", "끝", "점수", "이유", "미리보기"],
                    datatype=["str", "str", "number", "str", "str"],
                    wrap=True,
                )

        gr.Markdown("### 📥 결과 내려받기")
        with gr.Row():
            srt_out = gr.File(label="자막 (SRT)")
            edl_out = gr.File(label="컷 (EDL)")
            fcpxml_out = gr.File(label="컷 (FCPXML, beta)")
            review_out = gr.File(label="컷 미리보기 (MD)")
            json_out = gr.File(label="메타데이터 (JSON)")
            paper_out = gr.File(label="Paper edit (CSV)")
            decisions_out = gr.File(label="리뷰 결정표 (CSV)")
            html_out = gr.File(label="Director 리뷰 (HTML)")

        # 파일 업로드 시 경로 칸에 자동 반영
        uploaded.change(lambda f: f or "", inputs=uploaded, outputs=path_text)

        analyze_btn.click(
            process_video,
            inputs=[
                path_text, uploaded, model, device, compute_type,
                do_fillers, do_silence, do_retakes, do_shorts, fps, cut_preset, filler_mode, director_mode,
            ],
            outputs=[
                summary, sub_df, cut_df, cand_df, short_df,
                srt_out, edl_out, fcpxml_out, review_out, json_out, paper_out, decisions_out, html_out,
            ],
        )

        gr.Markdown(
            "---\n"
            "💡 **사용 팁**: 첫 실행 시 Whisper 모델(~3GB)이 자동 다운로드됩니다. "
            "GPU가 있으면 훨씬 빠릅니다. 자막이 과하게 끊기거나 간투사가 과검출되면 "
            "간투사 강도를 '보수적'으로 낮추거나 옵션을 끄세요."
        )

    return cast(gr.Blocks, demo)


def main() -> None:
    demo = build_ui()
    demo.launch(inbrowser=True, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
