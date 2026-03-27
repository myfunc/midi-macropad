"""Persistent JSON settings with named profile support — auto-saves on every put()."""
import json
import os
import shutil

_DIR = os.path.dirname(__file__)
_PATH = os.path.join(_DIR, "settings.json")
_PROFILES_DIR = os.path.join(_DIR, "profiles")
_data: dict = {}


def load():
    global _data
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            _data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _data = {}
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    if not list_profiles():
        save_profile("default")


def save():
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(_data, f, indent=2, ensure_ascii=False)


def get(key: str, default=None):
    return _data.get(key, default)


def put(key: str, value):
    _data[key] = value
    save()


def get_all() -> dict:
    return dict(_data)


def set_all(data: dict):
    global _data
    _data = dict(data)
    save()


# ── profiles ────────────────────────────────────────────────────────────────

def list_profiles() -> list[str]:
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    return sorted(
        f[:-5] for f in os.listdir(_PROFILES_DIR)
        if f.endswith(".json")
    )


def active_profile() -> str:
    return _data.get("active_profile", "default")


def save_profile(name: str | None = None):
    name = name or active_profile()
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_data, f, indent=2, ensure_ascii=False)
    if _data.get("active_profile") != name:
        _data["active_profile"] = name
        save()


def load_profile(name: str):
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    global _data
    try:
        with open(path, "r", encoding="utf-8") as f:
            _data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _data = {}
    _data["active_profile"] = name
    save()


def copy_profile(src: str, dst: str):
    src_path = os.path.join(_PROFILES_DIR, f"{src}.json")
    dst_path = os.path.join(_PROFILES_DIR, f"{dst}.json")
    if os.path.exists(src_path):
        shutil.copy2(src_path, dst_path)
