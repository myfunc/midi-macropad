"""One-shot migration: extract plaintext secrets from profiles into the vault.

Run automatically during `settings.load()`. Idempotent — once a profile has
${vault:...} placeholders instead of plaintext values, the migration skips it.

Handled keys:
  - profile["spotify_plugin"].{client_id, access_token, refresh_token, token_expires_at}
  - profile["obs_session_plugin"].password

After migration:
  - Real values are written to secrets.json (via vault.put)
  - Profile fields are replaced with ${vault:ns.key} placeholders
  - Profile is rewritten atomically
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from secrets_store import vault, make_vault_ref, is_vault_ref

log = logging.getLogger(__name__)

_SPOTIFY_KEYS = ("client_id", "access_token", "refresh_token", "token_expires_at")
_OBS_SECRET_KEYS = ("password",)


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _needs_migration(profile: dict) -> bool:
    sp = profile.get("spotify_plugin")
    if isinstance(sp, dict):
        for k in _SPOTIFY_KEYS:
            if k in sp and not is_vault_ref(sp[k]):
                # A non-ref, non-empty plaintext value → needs migration.
                if sp[k] not in (None, "", 0, 0.0):
                    return True
    obs = profile.get("obs_session_plugin")
    if isinstance(obs, dict):
        for k in _OBS_SECRET_KEYS:
            if k in obs and not is_vault_ref(obs[k]):
                if obs[k]:
                    return True
    return False


def _migrate_profile(profile: dict) -> bool:
    """Mutate profile in-place. Returns True if anything changed."""
    changed = False

    sp = profile.get("spotify_plugin")
    if isinstance(sp, dict):
        extracted: dict[str, Any] = {}
        for k in _SPOTIFY_KEYS:
            if k in sp and not is_vault_ref(sp[k]):
                extracted[k] = sp[k]
        if any(v not in (None, "", 0, 0.0) for v in extracted.values()):
            vault.put("spotify", extracted)
            for k in _SPOTIFY_KEYS:
                if k in sp:
                    sp[k] = make_vault_ref("spotify", k)
            changed = True

    obs = profile.get("obs_session_plugin")
    if isinstance(obs, dict):
        for k in _OBS_SECRET_KEYS:
            if k in obs and not is_vault_ref(obs[k]) and obs[k]:
                vault.set("obs_session", k, obs[k])
                obs[k] = make_vault_ref("obs_session", k)
                changed = True

    return changed


def migrate_all_profiles(profiles_dir: str | Path) -> int:
    """Scan profiles directory and migrate any plaintext secrets. Returns count."""
    profiles_path = Path(profiles_dir)
    if not profiles_path.is_dir():
        return 0

    migrated_count = 0
    for profile_file in profiles_path.glob("*.json"):
        try:
            raw = profile_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Skipping unreadable profile %s: %s", profile_file, exc)
            continue

        if not isinstance(data, dict) or not _needs_migration(data):
            continue

        if _migrate_profile(data):
            try:
                _atomic_write(profile_file, data)
                migrated_count += 1
                log.info("Migrated secrets out of profile %s", profile_file.name)
            except OSError as exc:
                log.error("Failed to rewrite profile %s: %s", profile_file, exc)

    return migrated_count


def migrate_settings_file(settings_path: str | Path) -> bool:
    """Migrate secrets out of the top-level settings.json if present."""
    path = Path(settings_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or not _needs_migration(data):
        return False
    if _migrate_profile(data):
        try:
            _atomic_write(path, data)
            log.info("Migrated secrets out of %s", path.name)
            return True
        except OSError as exc:
            log.error("Failed to rewrite %s: %s", path, exc)
    return False
