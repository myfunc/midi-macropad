import logging
import platform
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_CUTOFF = timedelta(days=30)
_now = datetime.now()
for p in LOG_DIR.glob("*.log"):
    try:
        st = p.stat()
        if st.st_size == 0:
            p.unlink(missing_ok=True)
            continue
        mtime = datetime.fromtimestamp(st.st_mtime)
        if _now - mtime > _CUTOFF:
            p.unlink(missing_ok=True)
    except OSError:
        pass

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"session_{_timestamp}.log"

_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
)

_thread_local = threading.local()


class _DuplicateThrottleFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._last_key: tuple[int, str] | None = None
        self._last_logger_name = "app"
        self._last_time = 0.0
        self._repeat_count = 0
        self._pending_suppress = 0

    def _emit_suppressed(self, level: int, logger_name: str, n: int) -> None:
        _thread_local.bypass = True
        try:
            logging.getLogger(logger_name).log(
                level,
                "[suppressed %d repeated identical messages]",
                n,
            )
        finally:
            _thread_local.bypass = False

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(_thread_local, "bypass", False):
            return True
        now = time.time()
        key = (record.name, record.levelno, record.getMessage())
        with self._lock:
            if self._last_key is None or key != self._last_key:
                if self._pending_suppress > 0 and self._last_key is not None:
                    self._emit_suppressed(
                        self._last_key[0], self._last_logger_name, self._pending_suppress
                    )
                self._last_key = key
                self._last_logger_name = record.name
                self._last_time = now
                self._repeat_count = 1
                self._pending_suppress = 0
                return True
            if now - self._last_time > 10.0:
                self._repeat_count = 0
            self._last_time = now
            self._repeat_count += 1
            if self._repeat_count > 3:
                self._pending_suppress += 1
                return False
            return True


_dup_filter = _DuplicateThrottleFilter()

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_formatter)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.addFilter(_dup_filter)
        logger.addHandler(_file_handler)
        logger.addHandler(_console_handler)
        logger.propagate = False
    return logger


def log_startup_banner(enabled_plugins: list[str], version: str | None = None) -> None:
    v = version or "unknown"
    log = get_logger("app")
    log.info(
        "Startup: app=%s python=%s platform=%s os=%s plugins=%s",
        v,
        sys.version.split()[0],
        platform.platform(),
        sys.platform,
        enabled_plugins,
    )


def log_session_summary(midi_events: int, plugin_errors: int, duration_s: float) -> None:
    log = get_logger("app")
    log.info(
        "Session: midi_events=%d plugin_errors=%d duration_s=%.1f",
        midi_events,
        plugin_errors,
        duration_s,
    )
