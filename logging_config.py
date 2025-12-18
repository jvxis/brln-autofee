import logging
import logging.handlers
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from functools import wraps
import time

PROJECT_ROOT = Path(__file__).parent.resolve()
LOG_DIR = PROJECT_ROOT / "logs"

LOG_LEVEL = os.getenv("BRLN_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("BRLN_LOG_FORMAT", "text").lower()
LOG_CONSOLE = os.getenv("BRLN_LOG_CONSOLE", "true").lower() == "true"
LOG_FILE = os.getenv("BRLN_LOG_FILE", "true").lower() == "true"

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

_initialized = False
_context: Dict[str, Any] = {}


class JSONFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if _context:
            log_data["context"] = _context.copy()

        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info)
            }

        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data

        return json.dumps(log_data, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self, use_colors: bool = False):
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname

        if self.use_colors and sys.stderr.isatty():
            color = self.COLORS.get(level, "")
            level_str = f"{color}{level:8s}{self.RESET}"
        else:
            level_str = f"{level:8s}"

        base_msg = f"{timestamp} | {level_str} | {record.name:24s} | {record.getMessage()}"

        if _context:
            ctx_str = " | ".join(f"{k}={v}" for k, v in _context.items())
            base_msg = f"{base_msg} | {ctx_str}"

        if record.exc_info:
            base_msg += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return base_msg


class BRLNLogger(logging.Logger):

    def _log_with_data(self, level: int, msg: str, data: Dict[str, Any] = None, **kwargs):
        if data:
            extra = kwargs.get("extra", {})
            extra["extra_data"] = data
            kwargs["extra"] = extra
        self.log(level, msg, **kwargs)

    def debug_data(self, msg: str, data: Dict[str, Any] = None, **kwargs):
        self._log_with_data(logging.DEBUG, msg, data, **kwargs)

    def info_data(self, msg: str, data: Dict[str, Any] = None, **kwargs):
        self._log_with_data(logging.INFO, msg, data, **kwargs)

    def warning_data(self, msg: str, data: Dict[str, Any] = None, **kwargs):
        self._log_with_data(logging.WARNING, msg, data, **kwargs)

    def error_data(self, msg: str, data: Dict[str, Any] = None, **kwargs):
        self._log_with_data(logging.ERROR, msg, data, **kwargs)


logging.setLoggerClass(BRLNLogger)


def set_context(**kwargs):
    _context.update(kwargs)


def clear_context():
    _context.clear()


def setup_logging(
    log_level: str = None,
    log_format: str = None,
    console: bool = None,
    file: bool = None,
    log_dir: Path = None
) -> None:
    global _initialized

    if _initialized:
        return

    level = (log_level or LOG_LEVEL).upper()
    fmt = (log_format or LOG_FORMAT).lower()
    enable_console = console if console is not None else LOG_CONSOLE
    enable_file = file if file is not None else LOG_FILE
    log_path = log_dir or LOG_DIR

    log_path.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("brln")
    root_logger.setLevel(getattr(logging, level, logging.INFO))
    root_logger.handlers.clear()

    if enable_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(getattr(logging, level, logging.INFO))

        if fmt == "json":
            console_handler.setFormatter(JSONFormatter())
        else:
            console_handler.setFormatter(TextFormatter(use_colors=True))

        root_logger.addHandler(console_handler)

    if enable_file:
        if fmt == "json":
            file_formatter = JSONFormatter()
        else:
            file_formatter = TextFormatter(use_colors=False)

        main_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "brln.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        main_handler.setLevel(getattr(logging, level, logging.INFO))
        main_handler.setFormatter(file_formatter)
        root_logger.addHandler(main_handler)

        error_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "brln.error.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        root_logger.addHandler(error_handler)

        if level == "DEBUG":
            debug_handler = logging.handlers.RotatingFileHandler(
                filename=log_path / "brln.debug.log",
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8"
            )
            debug_handler.setLevel(logging.DEBUG)
            debug_handler.setFormatter(file_formatter)
            root_logger.addHandler(debug_handler)

    _initialized = True
    root_logger.info(
        f"Logging inicializado: level={level}, format={fmt}, "
        f"console={enable_console}, file={enable_file}, dir={log_path}"
    )


def get_logger(name: str) -> BRLNLogger:
    if not _initialized:
        setup_logging()
    return logging.getLogger(f"brln.{name}")


def log_execution_time(logger: logging.Logger = None, level: int = logging.DEBUG):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = get_logger(func.__module__)

            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.log(level, f"{func.__name__} executado em {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                logger.error(f"{func.__name__} falhou após {elapsed:.3f}s: {e}")
                raise

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = get_logger(func.__module__)

            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.log(level, f"{func.__name__} executado em {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                logger.error(f"{func.__name__} falhou após {elapsed:.3f}s: {e}")
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    return decorator


if __name__ == "__main__":
    setup_logging(log_level="DEBUG")
    logger = get_logger("test")

    logger.debug("Mensagem de debug")
    logger.info("Mensagem de info")
    logger.warning("Mensagem de warning")
    logger.error("Mensagem de erro")

    set_context(request_id="abc123", channel="test-channel")
    logger.info("Mensagem com contexto")
    clear_context()

    logger.info_data("Mensagem com dados", {"key": "value", "number": 42})

    try:
        raise ValueError("Erro de teste")
    except Exception:
        logger.exception("Exceção capturada")
