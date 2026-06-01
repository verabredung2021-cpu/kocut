"""KoCut GUI — Gradio 기반 로컬 웹 인터페이스.

브라우저에서 열리는 간단한 GUI입니다. 영상 경로를 입력하거나 파일을 선택하면
자막·컷 후보·쇼츠 후보를 분석해 표로 보여주고, SRT/EDL/JSON을 내려받을 수
있습니다. KoCut 파이프라인을 그대로 호출하므로 CLI와 결과가 동일합니다.

실행:
    python -m kocut.gui
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

import gradio as gr

from kocut import audio, fillers, output, retakes, shorts, silence, subtitles, transcribe
from kocut.audio import FFmpegError
from kocut.transcribe import WhisperNotInstalledError
from kocut.types import Meta

_KIND_KR = {"filler": "간투사", "silence": "무음", "retake": "재촬영", "low_info": "저정보"}


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
    do_fillers: bool,
    do_silence: bool,
    do_retakes: bool,
    do_shorts: bool,
    fps: float,
    progress: gr.Progress = gr.Progress(),  # noqa: B008
) -> tuple[str, list[list[object]], list[list[object]], list[list[object]], str, str, str]:
    """영상을 분석해 (요약, 자막표, 컷표, 쇼츠표, srt, edl, json)을 반환합니다."""
    video = _resolve_path(path_text, uploaded)
    if not video.exists():
        raise gr.Error(f"파일을 찾을 수 없습니다: {video}")

    out_dir = Path(tempfile.mkdtemp(prefix="kocut_"))
    wav_path = out_dir / f"{video.stem}.kocut.wav"

    try:
        progress(0.05, desc="오디오 추출 중...")
        audio.extract_wav(video, wav_path)
        duration = audio.get_duration(video)

        progress(0.15, desc="한국어 트랜스크립션 중... (첫 실행은 모델 다운로드로 시간이 걸립니다)")
        segments = transcribe.transcribe_korean(str(wav_path), model_name=model, device=device)
        words = [w for seg in segments for w in seg.words]

        progress(0.7, desc="자막 분할 중...")
        subs = subtitles.split_subtitles(words)

        progress(0.78, desc="컷 후보 검출 중...")
        cuts = []
        if do_fillers:
            cuts += fillers.detect_fillers(words)
        if do_silence:
            cuts += silence.detect_silences(str(wav_path), words)
        if do_retakes:
            cuts += retakes.detect_retakes(segments)
        cuts.sort(key=lambda c: c.start)

        progress(0.88, desc="쇼츠 후보 점수 중...")
        shorts_list = shorts.score_shorts_candidates(segments) if do_shorts else []

        progress(0.95, desc="결과 파일 작성 중...")
        meta = Meta(
            source_path=str(video), duration=duration, model=model,
            segments=segments, subtitles=subs, cuts=cuts, shorts=shorts_list,
        )
        srt_path = output.write_srt(subs, out_dir / f"{video.stem}.srt")
        edl_path = output.write_edl(cuts, out_dir / f"{video.stem}.cuts.edl", duration, fps)
        json_path = output.write_meta_json(meta, out_dir / f"{video.stem}.meta.json")
    except FFmpegError as exc:
        raise gr.Error(f"오디오 처리 실패: {exc}")
    except WhisperNotInstalledError as exc:
        raise gr.Error(str(exc))
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"처리 중 오류: {exc}")
    finally:
        try:
            wav_path.unlink()
        except OSError:
            pass

    n_fill = sum(1 for c in cuts if c.kind == "filler")
    n_sil = sum(1 for c in cuts if c.kind == "silence")
    n_ret = sum(1 for c in cuts if c.kind == "retake")
    summary = (
        f"### ✅ 분석 완료\n"
        f"- 영상 길이: **{duration:.1f}초** ({duration/60:.1f}분)\n"
        f"- 자막: **{len(subs)}줄**\n"
        f"- 컷 후보: **{len(cuts)}개** (간투사 {n_fill} · 무음 {n_sil} · 재촬영 {n_ret})\n"
        f"- 쇼츠 후보: **{len(shorts_list)}개**\n\n"
        f"아래 탭에서 결과를 확인하고, SRT/EDL을 내려받아 Premiere·DaVinci에 import 하세요."
    )

    sub_rows: list[list[object]] = [[s.index, _fmt_time(s.start), _fmt_time(s.end), s.text] for s in subs]
    cut_rows: list[list[object]] = [
        [_fmt_time(c.start), _fmt_time(c.end), _KIND_KR.get(c.kind, c.kind), c.reason] for c in cuts
    ]
    short_rows: list[list[object]] = [
        [_fmt_time(c.start), _fmt_time(c.end), round(c.score, 1), c.reason, c.text]
        for c in shorts_list
    ]

    return (
        summary, sub_rows, cut_rows, short_rows,
        str(srt_path), str(edl_path), str(json_path),
    )


def build_ui() -> gr.Blocks:
    """GUI를 구성해 Blocks를 반환합니다."""
    with gr.Blocks(title="KoCut") as demo:
        gr.Markdown(
            "# 🎬 KoCut — 한국어 영상 자동 편집\n"
            "영상을 분석해 **자막**과 **컷 후보**(무음·간투사·재촬영), **쇼츠 후보**를 자동 추출합니다. "
            "원본 영상은 수정하지 않으며, 결과는 표준 SRT/EDL 파일로 내보냅니다."
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
                fps = gr.Dropdown(
                    [30.0, 29.97, 24.0, 23.976, 60.0],
                    value=30.0, label="EDL 프레임레이트",
                )

        with gr.Row():
            do_fillers = gr.Checkbox(value=True, label="간투사 검출 (어/음/그)")
            do_silence = gr.Checkbox(value=True, label="무음 검출")
            do_retakes = gr.Checkbox(value=True, label="재촬영 검출")
            do_shorts = gr.Checkbox(value=True, label="쇼츠 후보")

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
                gr.Markdown("검토 후 직접 적용하세요. EDL은 이 컷들을 **제외한** 구간만 이어 붙입니다.")
                cut_df = gr.Dataframe(
                    headers=["시작", "끝", "종류", "이유"],
                    datatype=["str", "str", "str", "str"],
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
            json_out = gr.File(label="메타데이터 (JSON)")

        # 파일 업로드 시 경로 칸에 자동 반영
        uploaded.change(lambda f: f or "", inputs=uploaded, outputs=path_text)

        analyze_btn.click(
            process_video,
            inputs=[path_text, uploaded, model, device, do_fillers, do_silence, do_retakes, do_shorts, fps],
            outputs=[summary, sub_df, cut_df, short_df, srt_out, edl_out, json_out],
        )

        gr.Markdown(
            "---\n"
            "💡 **사용 팁**: 첫 실행 시 Whisper 모델(~3GB)이 자동 다운로드됩니다. "
            "GPU가 있으면 훨씬 빠릅니다. 자막이 과하게 끊기거나 간투사가 과검출되면 "
            "옵션을 끄거나 `kocut/fillers.py`의 단어 목록을 조정하세요."
        )

    return cast(gr.Blocks, demo)


def main() -> None:
    demo = build_ui()
    demo.launch(inbrowser=True, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
