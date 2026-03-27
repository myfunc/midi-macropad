import json
import threading

import pytest


@pytest.fixture
def settings_mod(tmp_path, monkeypatch):
    import settings as s

    monkeypatch.setattr(s, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(s, "_PROFILES_DIR", str(tmp_path / "profiles"))
    with s._lock:
        s._cancel_timer_locked()
        s._data = {}
        s._dirty_settings = False
    yield s
    with s._lock:
        s._cancel_timer_locked()
        s._data = {}
        s._dirty_settings = False


def _seed_profiles_nonempty(tmp_path):
    prof = tmp_path / "profiles"
    prof.mkdir(parents=True)
    (prof / "skip.json").write_text("{}", encoding="utf-8")


def test_load_empty_when_no_file(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    settings_mod.load()
    assert settings_mod.get_all() == {}


def test_load_corrupt_json_returns_empty(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    settings_mod.load()
    assert settings_mod.get_all() == {}


def test_put_get_roundtrip(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    settings_mod.load()
    settings_mod.put("a", 1)
    assert settings_mod.get("a") == 1
    assert settings_mod.get("missing", "x") == "x"


def test_put_flush_writes_file(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    settings_mod.load()
    settings_mod.put("k", "v")
    settings_mod.flush()
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert json.loads(raw)["k"] == "v"


def test_save_atomic_valid_json_immediately(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    settings_mod.load()
    settings_mod.put("n", 42)
    settings_mod.save()
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert json.loads(raw)["n"] == 42


def test_profile_crud(settings_mod, tmp_path):
    settings_mod.load()
    settings_mod.put("foo", "bar")
    settings_mod.save_profile("alpha")
    settings_mod.set_all({})
    settings_mod.load_profile("alpha")
    assert settings_mod.get("foo") == "bar"
    assert settings_mod.get("active_profile") == "alpha"
    names = settings_mod.list_profiles()
    assert "alpha" in names
    assert "default" in names
    settings_mod.copy_profile("alpha", "beta")
    settings_mod.load_profile("beta")
    assert settings_mod.get("foo") == "bar"


def test_set_all_replaces_dict(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    settings_mod.load()
    settings_mod.put("old", True)
    settings_mod.set_all({"only": 1})
    assert settings_mod.get_all() == {"only": 1}


def test_concurrent_put_no_corruption(settings_mod, tmp_path):
    _seed_profiles_nonempty(tmp_path)
    settings_mod.load()

    def worker(tid: int):
        for i in range(40):
            settings_mod.put(f"t{tid}_{i}", tid * 1000 + i)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    settings_mod.flush()
    data = settings_mod.get_all()
    assert len(data) == 320
    for tid in range(8):
        for i in range(40):
            assert data[f"t{tid}_{i}"] == tid * 1000 + i
