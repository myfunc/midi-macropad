"""Persistent JSON settings — auto-saves on every put()."""
import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
_data: dict = {}


def load():
    global _data
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            _data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _data = {}


def save():
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(_data, f, indent=2, ensure_ascii=False)


def get(key: str, default=None):
    return _data.get(key, default)


def put(key: str, value):
    _data[key] = value
    save()
