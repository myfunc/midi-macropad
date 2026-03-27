"""Persistent JSON settings with named profile support — debounced save, atomic writes."""
import json
import logging
import os
import shutil
import tempfile
import threading

_DIR = os.path.dirname(__file__)
_PATH = os.path.join(_DIR, "settings.json")
_PROFILES_DIR = os.path.join(_DIR, "profiles")
_DEBOUNCE_S = 0.5

_lock = threading.Lock()
_data: dict = {}
_dirty_settings = False
_flush_timer: threading.Timer | None = None

_log = logging.getLogger(__name__)


def _cancel_timer_locked() -> None:
    global _flush_timer
    if _flush_timer is not None:
        _flush_timer.cancel()
        _flush_timer = None


def _schedule_flush_locked() -> None:
    global _flush_timer
    _cancel_timer_locked()

    def _cb() -> None:
        try:
            flush()
        except Exception:
            _log.exception("settings flush failed")

    _flush_timer = threading.Timer(_DEBOUNCE_S, _cb)
    _flush_timer.daemon = True
    _flush_timer.start()


def _atomic_write_json(path: str, data: dict) -> None:
    dn = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dn, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".settings_", suffix=".tmp", dir=dn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_settings_unlocked() -> None:
    _atomic_write_json(_PATH, _data)


def load():
    global _data
    with _lock:
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    if not list_profiles():
        save_profile("default")


def save():
    global _dirty_settings
    with _lock:
        _cancel_timer_locked()
        _write_settings_unlocked()
        _dirty_settings = False


def flush():
    global _dirty_settings
    with _lock:
        _cancel_timer_locked()
        if _dirty_settings:
            _write_settings_unlocked()
            _dirty_settings = False


def get(key: str, default=None):
    with _lock:
        return _data.get(key, default)


def put(key: str, value):
    global _dirty_settings
    with _lock:
        _data[key] = value
        _dirty_settings = True
        _schedule_flush_locked()


def get_all() -> dict:
    with _lock:
        return dict(_data)


def set_all(data: dict):
    global _data, _dirty_settings
    with _lock:
        _data = dict(data)
        _dirty_settings = True
        _schedule_flush_locked()


def list_profiles() -> list[str]:
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    with _lock:
        return sorted(
            f[:-5] for f in os.listdir(_PROFILES_DIR)
            if f.endswith(".json")
        )


def active_profile() -> str:
    with _lock:
        return _data.get("active_profile", "default")


def save_profile(name: str | None = None):
    global _dirty_settings
    with _lock:
        name = name or _data.get("active_profile", "default")
        path = os.path.join(_PROFILES_DIR, f"{name}.json")
        os.makedirs(_PROFILES_DIR, exist_ok=True)
        _atomic_write_json(path, _data)
        if _data.get("active_profile") != name:
            _data["active_profile"] = name
            _cancel_timer_locked()
            _write_settings_unlocked()
            _dirty_settings = False


def load_profile(name: str):
    global _data, _dirty_settings
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
        _data["active_profile"] = name
        _cancel_timer_locked()
        _write_settings_unlocked()
        _dirty_settings = False


def copy_profile(src: str, dst: str):
    with _lock:
        src_path = os.path.join(_PROFILES_DIR, f"{src}.json")
        dst_path = os.path.join(_PROFILES_DIR, f"{dst}.json")
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst_path)
