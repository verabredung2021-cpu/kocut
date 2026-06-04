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


def _same_file_handler(handler: logging.Handler, log_file: Path) -> bool:
    if not isinstance(handler, logging.FileHandler):
        return False
    try:
        return Path(handler.baseFilename).resolve() == log_file.resolve()
    except OSError:
        return False


def get_logger(name: str = "kocut", *, log_file: Path | None = None, verbose: bool = False) -> logging.Logger:
    """이름별 로거를 반환합니다.

    같은 로거를 여러 번 요청해도 콘솔 핸들러는 중복 추가하지 않습니다. 다만 이전
    구현은 로거가 이미 만들어진 뒤 새 ``log_file``을 넘기면 파일 핸들러가 추가되지
    않았습니다. GUI/테스트처럼 한 프로세스에서 여러 파일을 처리할 때 로그가 엉뚱한
    파일로 가거나 아예 남지 않는 문제를 막기 위해, 요청된 파일 핸들러는 매번 확인해
    필요하면 추가합니다.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    console_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
    if not console_handlers:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)
        console_handlers = [console]

    for handler in console_handlers:
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    if log_file is not None:
        log_file = log_file.expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if not any(_same_file_handler(handler, log_file) for handler in logger.handlers):
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
            logger.addHandler(file_handler)
        for existing_handler in logger.handlers:
            if isinstance(existing_handler, logging.FileHandler):
                existing_handler.setLevel(logging.DEBUG)

    return logger
