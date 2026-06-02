"""faster-whisper 래퍼.

한국어 음성을 단어 단위 타임스탬프와 함께 트랜스크립션합니다. 언어를 명시적으로
'ko'로 고정해 (auto-detect 금지 — 영상 앞부분 음악/침묵 때문에 다른 언어로
오인식되는 문제 방지) 합니다. 모델은 첫 사용 시점에 lazy 로딩합니다.

faster-whisper와 모델 가중치는 사용자 PC에 설치/다운로드됩니다. 이 모듈은
무거운 의존성을 모듈 import 시점이 아니라 함수 호출 시점에 import 합니다.
"""
from __future__ import annotations

import math
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kocut.types import Segment, Word


class WhisperNotInstalledError(RuntimeError):
    """faster-whisper가 설치되지 않음."""


_nvidia_dll_registered = False
_nvidia_dll_handles: list[object] = []


def _register_nvidia_dll_dirs() -> None:
    """Windows에서 nvidia-* pip 패키지(cuBLAS/cuDNN)의 DLL 경로를 검색 경로에 등록합니다.

    nvidia-cublas-cu12 / nvidia-cudnn-cu12를 pip로 설치하면 DLL이
    site-packages/nvidia/<lib>/bin 에 들어가는데, ctranslate2가 이 위치를
    자동으로 찾지 못해 'cublas64_12.dll is not found' 오류가 납니다. 해당
    디렉토리를 os.add_dll_directory로 등록해 GPU 사용을 가능하게 합니다.
    (Windows 전용, 한 번만 수행)
    """
    global _nvidia_dll_registered
    if _nvidia_dll_registered or sys.platform != "win32":
        return
    _nvidia_dll_registered = True
    try:
        import site  # noqa: PLC0415

        bases: list[str] = list(site.getsitepackages())
        user_site = site.getusersitepackages()
        if isinstance(user_site, str):
            bases.append(user_site)

        seen: set[str] = set()
        for base in bases:
            nvidia_dir = Path(base) / "nvidia"
            if not nvidia_dir.is_dir():
                continue
            for sub in nvidia_dir.iterdir():
                bin_dir = sub / "bin"
                key = str(bin_dir)
                if bin_dir.is_dir() and key not in seen:
                    seen.add(key)
                    try:
                        # 반환된 핸들을 보관해야 Windows DLL 검색 경로가 유지됩니다.
                        _nvidia_dll_handles.append(os.add_dll_directory(key))
                    except OSError:
                        pass
    except Exception:  # noqa: BLE001  (DLL 경로 등록 실패는 치명적이지 않음)
        pass


def _safe_time(value: object, fallback: float) -> float:
    """None/NaN/inf 타임스탬프를 안전한 유한 float로 변환합니다.

    faster-whisper는 word_timestamps 사용 시 일부 단어의 start/end를 None이나
    NaN으로 반환할 때가 있습니다. 이를 방치하면 자막 시간이 깨지거나(NaN),
    JSON 직렬화가 비표준(NaN 토큰)이 되어 다른 도구가 읽지 못합니다.
    """
    if value is None:
        return fallback
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(f):
        return fallback
    return f


def _load_model(model_name: str, device: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415  (의도적 lazy import)
    except ImportError as exc:
        raise WhisperNotInstalledError(
            "faster-whisper가 설치되지 않았습니다. "
            "`pip install faster-whisper` 로 설치하세요."
        ) from exc
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def _resolve_auto_device(device: str, compute_type: str) -> tuple[str, str]:
    """device='auto'를 실제 (device, compute_type)으로 해석합니다.

    GPU가 감지되면 cuda(float16), 아니면 cpu(int8)를 씁니다. 명시적으로 지정한
    경우 그대로 사용하되 compute_type이 auto면 장치에 맞는 기본값을 채웁니다.
    """
    if device != "auto":
        if compute_type != "auto":
            return device, compute_type
        return device, ("float16" if device == "cuda" else "int8")
    try:
        import ctranslate2  # noqa: PLC0415

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:  # noqa: BLE001  (CUDA 미설치/탐지 실패 시 CPU로)
        pass
    return "cpu", "int8"


# GPU 관련 실패로 판단할 에러 메시지 힌트 (cuBLAS/cuDNN DLL 누락 등)
_CUDA_ERROR_HINTS = (
    "cublas", "cudnn", "cuda", "gpu", ".dll", "libcu", "no kernel",
    "out of memory", "failed to allocate", "allocator",
)


def _looks_like_cuda_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _CUDA_ERROR_HINTS)


def _run_transcription(
    wav_path: str, model_name: str, device: str, compute_type: str, language: str
) -> list[Any]:
    """추론을 끝까지 수행해 raw 세그먼트 리스트를 반환합니다.

    faster-whisper의 transcribe()는 지연 제너레이터를 반환하므로, list()로
    완전히 소비하는 이 시점에 실제 GPU 연산(및 cuBLAS 로딩)이 일어납니다.
    """
    model = _load_model(model_name, device, compute_type)
    segments, _info = model.transcribe(
        wav_path,
        language=language,  # 한국어 고정
        word_timestamps=True,
        vad_filter=True,  # 무음/비음성 구간 필터링으로 hallucination 감소
        vad_parameters={"min_silence_duration_ms": 500},
    )
    return list(segments)


def iter_segments(
    wav_path: str,
    *,
    model_name: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    language: str = "ko",
) -> Iterator[Segment]:
    """WAV를 트랜스크립션하며 세그먼트를 하나씩 yield 합니다.

    device='auto'(기본)면 GPU를 우선 시도하고, cuBLAS/cuDNN 누락 등 GPU 관련
    오류가 나면 자동으로 CPU로 재시도합니다.
    """
    dev, ct = _resolve_auto_device(device, compute_type)
    if dev == "cuda":
        _register_nvidia_dll_dirs()  # pip 설치된 cuBLAS/cuDNN DLL 경로 등록
    try:
        raw_segments = _run_transcription(wav_path, model_name, dev, ct, language)
    except WhisperNotInstalledError:
        raise
    except Exception as exc:  # noqa: BLE001
        if dev == "cuda" and _looks_like_cuda_error(exc):
            # GPU 연산 실패 → CPU로 자동 전환 (cuBLAS/cuDNN 미설치 등)
            raw_segments = _run_transcription(wav_path, model_name, "cpu", "int8", language)
        else:
            raise

    prev_end = 0.0
    for seg in raw_segments:
        seg_start = _safe_time(seg.start, prev_end)
        seg_end = _safe_time(seg.end, seg_start + 0.05)
        if seg_end < seg_start:
            seg_end = seg_start + 0.05

        words: list[Word] = []
        word_cursor = seg_start
        for w in (seg.words or []):
            w_start = _safe_time(w.start, word_cursor)
            w_end = _safe_time(w.end, w_start + 0.05)
            if w_end < w_start:
                w_end = w_start + 0.05
            prob = _safe_time(w.probability, 0.0) if w.probability is not None else None
            words.append(Word(word=w.word, start=w_start, end=w_end, prob=prob))
            word_cursor = w_end

        prev_end = seg_end
        yield Segment(
            start=seg_start,
            end=seg_end,
            text=seg.text.strip(),
            words=words,
        )


def transcribe_korean(
    wav_path: str,
    *,
    model_name: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
) -> list[Segment]:
    """WAV 전체를 트랜스크립션해 세그먼트 리스트로 반환합니다."""
    return list(
        iter_segments(
            wav_path,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            language="ko",
        )
    )
