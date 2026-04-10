"""Secrets vault — single source of truth for plugin credentials.

Stores tokens, API keys, and passwords in `secrets.json` (gitignored).
Profiles reference vault entries via `${vault:namespace.key}` placeholders,
which `settings.get()` resolves at read time.

Fallback chain for reads:
  1. secrets.json (primary, auto-written)
  2. .env file (static keys like OPENAI_API_KEY)
  3. os.environ (for containers / CI)

Writes always go to secrets.json via atomic write.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from typing import Any

_DIR = os.path.dirname(__file__)
_PATH = os.path.join(_DIR, "secrets.json")
_ENV_PATH = os.path.join(_DIR, ".env")

_VAULT_REF_RE = re.compile(r"^\$\{vault:([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\}$")

_log = logging.getLogger(__name__)


def _atomic_write_json(path: str, data: dict) -> None:
    dn = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dn, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".secrets_", suffix=".tmp", dir=dn)
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


def _read_env_file() -> dict[str, str]:
    """Parse simple KEY=value lines from .env, ignoring comments and blanks."""
    result: dict[str, str] = {}
    if not os.path.exists(_ENV_PATH):
        return result
    try:
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip().strip('"').strip("'")
    except OSError as exc:
        _log.warning("Failed to read .env: %s", exc)
    return result


class SecretsVault:
    """Thread-safe singleton vault for plugin secrets.

    Namespaces map to plugins (e.g., "spotify", "obs_session", "voice_scribe").
    Each namespace is a flat dict of string keys to primitive values.
    """

    def __init__(self, path: str = _PATH):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._env_cache: dict[str, str] | None = None
        self._loaded = False

    def load(self) -> None:
        """Load secrets.json into memory. Idempotent."""
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        self._data = {
                            k: dict(v) for k, v in raw.items()
                            if isinstance(v, dict)
                        }
                    else:
                        self._data = {}
            except (FileNotFoundError, json.JSONDecodeError):
                self._data = {}
            self._env_cache = _read_env_file()
            self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _env_key(self, namespace: str, key: str) -> str:
        """Convention: ${NAMESPACE_KEY} in uppercase for env lookup."""
        return f"{namespace}_{key}".upper()

    def get(self, namespace: str, key: str | None = None, default: Any = None) -> Any:
        """Read a secret. If key is None, returns the whole namespace dict.

        Fallback: secrets.json → .env → os.environ → default.
        """
        self._ensure_loaded()
        with self._lock:
            ns = self._data.get(namespace, {})
            if key is None:
                # Return a copy of the whole namespace
                return dict(ns)
            if key in ns and ns[key]:
                return ns[key]
            env_key = self._env_key(namespace, key)
            # .env file
            if self._env_cache and env_key in self._env_cache:
                return self._env_cache[env_key]
            # os.environ
            if env_key in os.environ:
                return os.environ[env_key]
            return default

    def has(self, namespace: str, key: str) -> bool:
        """Return True if a non-empty value exists for namespace.key."""
        val = self.get(namespace, key, default=None)
        return val is not None and val != ""

    def put(self, namespace: str, data: dict[str, Any]) -> None:
        """Merge `data` into the namespace and persist."""
        self._ensure_loaded()
        with self._lock:
            existing = self._data.setdefault(namespace, {})
            existing.update(data)
            _atomic_write_json(self._path, self._data)

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Set a single key within a namespace and persist."""
        self.put(namespace, {key: value})

    def clear(self, namespace: str, key: str | None = None) -> None:
        """Remove a key or entire namespace. Persists the change."""
        self._ensure_loaded()
        with self._lock:
            if key is None:
                self._data.pop(namespace, None)
            else:
                ns = self._data.get(namespace)
                if ns is not None:
                    ns.pop(key, None)
                    if not ns:
                        self._data.pop(namespace, None)
            _atomic_write_json(self._path, self._data)

    def all_namespaces(self) -> list[str]:
        self._ensure_loaded()
        with self._lock:
            return sorted(self._data.keys())


# Module-level singleton
vault = SecretsVault()


# ---------------------------------------------------------------------------
# ${vault:ns.key} reference resolution (used by settings.get)
# ---------------------------------------------------------------------------

def is_vault_ref(value: Any) -> bool:
    """Return True if value is a string shaped like ${vault:ns.key}."""
    return isinstance(value, str) and bool(_VAULT_REF_RE.match(value))


def parse_vault_ref(value: str) -> tuple[str, str] | None:
    """Parse ${vault:ns.key} into (namespace, key). Returns None if not a ref."""
    m = _VAULT_REF_RE.match(value)
    if not m:
        return None
    return m.group(1), m.group(2)


def make_vault_ref(namespace: str, key: str) -> str:
    """Build a ${vault:ns.key} reference string."""
    return f"${{vault:{namespace}.{key}}}"


def resolve_value(value: Any) -> Any:
    """Walk a value and replace ${vault:...} references with real secrets.

    Recurses into dicts and lists. Non-string and non-ref values pass through.
    """
    if isinstance(value, dict):
        return {k: resolve_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v) for v in value]
    if isinstance(value, str):
        ref = parse_vault_ref(value)
        if ref is not None:
            ns, key = ref
            resolved = vault.get(ns, key, default="")
            return resolved
    return value
