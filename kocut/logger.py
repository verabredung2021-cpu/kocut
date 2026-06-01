"""KoCut 로깅.

한국어 메시지를 콘솔과 로그 파일에 기록합니다. 로그 파일은 timestamp +
모듈명 + 레벨을 포함해 나중에 문제를 추적할 수 있게 합니다.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str = "kocut", *, log_file: Path | None = None, verbose: bool = False) -> logging.Logger:
    """이름별 로거를 반환합니다. 같은 이름이면 핸들러를 중복 추가하지 않습니다."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.handlers:
        return logger

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger
