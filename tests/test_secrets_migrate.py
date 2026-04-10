import json
from pathlib import Path

import pytest

from secrets_store import SecretsVault, is_vault_ref, parse_vault_ref
import secrets_store
import secrets_migrate


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    v = SecretsVault(path=str(path))
    v.load()
    monkeypatch.setattr(secrets_store, "vault", v)
    monkeypatch.setattr(secrets_migrate, "vault", v)
    return v


def _write_profile(dir_: Path, name: str, data: dict) -> Path:
    dir_.mkdir(exist_ok=True)
    path = dir_ / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def test_migrate_spotify_tokens(tmp_path, vault):
    profiles_dir = tmp_path / "profiles"
    _write_profile(profiles_dir, "default", {
        "spotify_plugin": {
            "client_id": "cid-xyz",
            "access_token": "tok-abc",
            "refresh_token": "ref-def",
            "token_expires_at": 999.5,
            "redirect_port": 8765,
        },
        "window_width": 1200,
    })

    count = secrets_migrate.migrate_all_profiles(profiles_dir)
    assert count == 1

    # Vault now holds the real values
    assert vault.get("spotify", "client_id") == "cid-xyz"
    assert vault.get("spotify", "access_token") == "tok-abc"
    assert vault.get("spotify", "refresh_token") == "ref-def"
    assert vault.get("spotify", "token_expires_at") == 999.5

    # Profile now holds placeholders; non-secret fields remain
    reloaded = json.loads((profiles_dir / "default.json").read_text(encoding="utf-8"))
    sp = reloaded["spotify_plugin"]
    assert is_vault_ref(sp["client_id"])
    assert parse_vault_ref(sp["client_id"]) == ("spotify", "client_id")
    assert is_vault_ref(sp["access_token"])
    assert sp["redirect_port"] == 8765
    assert reloaded["window_width"] == 1200


def test_migrate_obs_password(tmp_path, vault):
    profiles_dir = tmp_path / "profiles"
    _write_profile(profiles_dir, "default", {
        "obs_session_plugin": {
            "host": "127.0.0.1",
            "port": 4455,
            "password": "supersecret",
            "scene_screen": "Screen",
        },
    })

    count = secrets_migrate.migrate_all_profiles(profiles_dir)
    assert count == 1
    assert vault.get("obs_session", "password") == "supersecret"

    reloaded = json.loads((profiles_dir / "default.json").read_text(encoding="utf-8"))
    obs = reloaded["obs_session_plugin"]
    assert is_vault_ref(obs["password"])
    assert obs["host"] == "127.0.0.1"
    assert obs["port"] == 4455
    assert obs["scene_screen"] == "Screen"


def test_migration_is_idempotent(tmp_path, vault):
    profiles_dir = tmp_path / "profiles"
    _write_profile(profiles_dir, "default", {
        "spotify_plugin": {"access_token": "tok"},
    })

    first = secrets_migrate.migrate_all_profiles(profiles_dir)
    second = secrets_migrate.migrate_all_profiles(profiles_dir)
    assert first == 1
    assert second == 0  # already migrated


def test_empty_secrets_not_migrated(tmp_path, vault):
    profiles_dir = tmp_path / "profiles"
    _write_profile(profiles_dir, "default", {
        "spotify_plugin": {"access_token": "", "refresh_token": ""},
        "obs_session_plugin": {"password": ""},
    })

    count = secrets_migrate.migrate_all_profiles(profiles_dir)
    assert count == 0
    assert vault.get("spotify", "access_token", default="MISSING") == "MISSING"


def test_profile_with_placeholders_not_migrated(tmp_path, vault):
    profiles_dir = tmp_path / "profiles"
    _write_profile(profiles_dir, "default", {
        "spotify_plugin": {
            "access_token": "${vault:spotify.access_token}",
        },
    })

    count = secrets_migrate.migrate_all_profiles(profiles_dir)
    assert count == 0
