import json
from pathlib import Path

import pytest

from secrets_store import (
    SecretsVault,
    is_vault_ref,
    make_vault_ref,
    parse_vault_ref,
    resolve_value,
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    v = SecretsVault(path=str(path))
    v.load()
    # Make resolve_value() use this vault instance instead of the module
    # singleton by monkey-patching.
    import secrets_store
    monkeypatch.setattr(secrets_store, "vault", v)
    return v


def test_put_and_get_single_value(vault):
    vault.set("spotify", "access_token", "tok-123")
    assert vault.get("spotify", "access_token") == "tok-123"


def test_put_merges_namespace(vault):
    vault.put("spotify", {"access_token": "tok", "refresh_token": "ref"})
    vault.put("spotify", {"token_expires_at": 12345.6})
    assert vault.get("spotify", "access_token") == "tok"
    assert vault.get("spotify", "refresh_token") == "ref"
    assert vault.get("spotify", "token_expires_at") == 12345.6


def test_get_entire_namespace(vault):
    vault.put("obs_session", {"password": "pw"})
    ns = vault.get("obs_session")
    assert ns == {"password": "pw"}
    # Returned dict is a copy — mutation does not affect the vault
    ns["password"] = "hacked"
    assert vault.get("obs_session", "password") == "pw"


def test_get_missing_returns_default(vault):
    assert vault.get("missing", "nope", default="fallback") == "fallback"


def test_clear_key(vault):
    vault.put("spotify", {"access_token": "tok", "refresh_token": "ref"})
    vault.clear("spotify", "access_token")
    assert vault.get("spotify", "access_token", default="") == ""
    assert vault.get("spotify", "refresh_token") == "ref"


def test_clear_namespace(vault):
    vault.put("spotify", {"access_token": "tok"})
    vault.clear("spotify")
    assert vault.get("spotify", "access_token", default="") == ""
    assert "spotify" not in vault.all_namespaces()


def test_persists_across_instances(tmp_path):
    path = tmp_path / "secrets.json"
    v1 = SecretsVault(path=str(path))
    v1.load()
    v1.set("spotify", "refresh_token", "persisted")

    v2 = SecretsVault(path=str(path))
    v2.load()
    assert v2.get("spotify", "refresh_token") == "persisted"


def test_env_fallback(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("VOICE_SCRIBE_OPENAI_API_KEY", "sk-env-value")
    v = SecretsVault(path=str(path))
    v.load()
    assert v.get("voice_scribe", "openai_api_key") == "sk-env-value"


def test_has_empty_returns_false(vault):
    assert vault.has("spotify", "access_token") is False
    vault.set("spotify", "access_token", "")
    assert vault.has("spotify", "access_token") is False
    vault.set("spotify", "access_token", "tok")
    assert vault.has("spotify", "access_token") is True


# ---------------------------------------------------------------------------
# Vault reference parsing & resolution
# ---------------------------------------------------------------------------

def test_make_and_parse_ref():
    ref = make_vault_ref("spotify", "access_token")
    assert ref == "${vault:spotify.access_token}"
    assert is_vault_ref(ref)
    assert parse_vault_ref(ref) == ("spotify", "access_token")


def test_is_vault_ref_rejects_plain_strings():
    assert not is_vault_ref("plain")
    assert not is_vault_ref("${other:foo.bar}")
    assert not is_vault_ref("prefix${vault:x.y}suffix")
    assert not is_vault_ref(123)
    assert not is_vault_ref(None)


def test_resolve_value_replaces_refs(vault):
    vault.set("spotify", "access_token", "resolved")
    assert resolve_value("${vault:spotify.access_token}") == "resolved"


def test_resolve_value_recurses_into_dict(vault):
    vault.set("spotify", "client_id", "cid-1")
    vault.set("obs_session", "password", "pw")
    data = {
        "spotify_plugin": {
            "client_id": "${vault:spotify.client_id}",
            "redirect_port": 8765,
        },
        "obs_session_plugin": {
            "host": "127.0.0.1",
            "password": "${vault:obs_session.password}",
        },
    }
    resolved = resolve_value(data)
    assert resolved["spotify_plugin"]["client_id"] == "cid-1"
    assert resolved["spotify_plugin"]["redirect_port"] == 8765
    assert resolved["obs_session_plugin"]["password"] == "pw"
    assert resolved["obs_session_plugin"]["host"] == "127.0.0.1"


def test_resolve_value_missing_ref_returns_empty(vault):
    # Missing vault key resolves to "" (the default) — plugin can treat as absent
    assert resolve_value("${vault:spotify.missing}") == ""


def test_resolve_value_passes_through_non_strings(vault):
    assert resolve_value(42) == 42
    assert resolve_value(None) is None
    assert resolve_value([1, 2, 3]) == [1, 2, 3]


def test_resolve_value_recurses_into_list(vault):
    vault.set("spotify", "access_token", "tok")
    data = [{"t": "${vault:spotify.access_token}"}, "plain"]
    assert resolve_value(data) == [{"t": "tok"}, "plain"]
