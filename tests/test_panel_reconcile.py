"""Tests for AppCore.reconcile_panels()."""
from __future__ import annotations

import threading

import pytest

from mapper import (
    ActionDef, AppConfig, KnobMapping, KnobPreset,
    Mapper, PadMapping, PadPreset,
)


def _fresh_core(monkeypatch, tmp_path):
    """Build a minimally-initialized AppCore that bypasses bootstrap."""
    import settings as _settings
    import backend.core as core_mod

    monkeypatch.setattr(_settings, "_PATH",
        tmp_path / "settings.json", raising=False)
    _settings._data = {}

    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(name="Main", pads=[
            PadMapping(note=16, label="A16",
                action=ActionDef(type="keystroke", keys="a")),
        ]),
    ]
    cfg.knob_presets = [
        KnobPreset(name="Master", knobs=[
            KnobMapping(cc=48, label="Vol",
                action=ActionDef(type="volume", target="master")),
        ]),
    ]

    core = core_mod.AppCore.__new__(core_mod.AppCore)
    core._lock = threading.RLock()
    core._active_pad_bank_mem = None
    core.log_buffer = []
    core._LOG_BUFFER_MAX = 200
    core.config = cfg
    core.mapper = Mapper(cfg)

    class _Bus:
        events: list = []
        def publish(self, name, payload=None):
            self.events.append((name, payload))
    core.event_bus = _Bus()

    class _PM:
        enabled = []
        plugins = {}
        def on_pad_press(self, *a, **kw): return False
        def on_knob(self, *a, **kw): return False
        def on_pad_release(self, *a, **kw): return False
        def on_pitch_bend(self, *a, **kw): return None
        def on_mode_changed(self, *a, **kw): return None
        def notify_preset_changed(self, *a, **kw): return None
        def get_all_pad_labels(self): return {}
        def get_pad_labels_by_plugin(self): return {}
        def get_pad_states_by_plugin(self): return {}
        def discover(self): return []
    core.plugin_manager = _PM()
    core._execute_action = lambda action: None

    class _Obs:
        connected = False
        current_scene = ""
        is_recording = False
        is_streaming = False
        is_replay_buffer_active = False
        scene_names: list = []
    core.obs = _Obs()

    class _Midi:
        connected = False
        port_name = ""
    core.midi = _Midi()
    return core


def test_reconcile_clears_stale_active_slot(monkeypatch, tmp_path):
    """active_panels pointing at a non-existent panel should be cleared."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)

    # Create one real panel, then inject a stale slot pointing at a ghost id.
    p = core.create_panel("pad", bank="A", preset="Main", activate=True)
    active = dict(_settings.get("active_panels") or {})
    active["pad:B"] = "ghost-id-does-not-exist"
    _settings.put("active_panels", active)
    # Also put it in mapper to verify mirror clears
    core.mapper.set_active_panel("pad", "B", "ghost-id-does-not-exist")

    result = core.reconcile_panels()

    after = _settings.get("active_panels") or {}
    assert after.get("pad:B") in (None, "")
    assert core.mapper.get_active_panel("pad", "B") is None
    # Live panel 'p' still active on pad:A
    assert after.get("pad:A") == p["instanceId"]
    assert any("stale" in msg for msg in result["fixed"])


def test_reconcile_assigns_unique_panel_to_empty_slot(monkeypatch, tmp_path):
    """If exactly one live panel matches an empty slot, it should auto-activate."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)

    # Create panel but don't activate it — leave slot empty.
    p = core.create_panel("pad", bank="A", preset="Main", activate=False)
    active = _settings.get("active_panels") or {}
    assert not active.get("pad:A")

    result = core.reconcile_panels()

    after = _settings.get("active_panels") or {}
    assert after.get("pad:A") == p["instanceId"]
    assert core.mapper.get_active_panel("pad", "A") == p["instanceId"]
    assert any("auto-activated" in msg for msg in result["fixed"])


def test_reconcile_fix_titles_template_only(monkeypatch, tmp_path):
    """fix_titles rewrites template titles but preserves custom ones."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)

    # Create two panels; manually warp one's bank/title to simulate drift.
    p_tpl = core.create_panel("pad", bank="A", preset="Main")
    p_custom = core.create_panel("knob", bank="A", preset="Master")

    panels = dict(_settings.get("panels") or {})
    # Template-titled panel: bank says B but title still says A -> should fix.
    panels[p_tpl["instanceId"]]["bank"] = "B"
    panels[p_tpl["instanceId"]]["title"] = "Pad Panel A"
    # Custom-titled panel: bank says B but custom title should be preserved.
    panels[p_custom["instanceId"]]["bank"] = "B"
    panels[p_custom["instanceId"]]["title"] = "My Cool Knobs"
    _settings.put("panels", panels)

    result = core.reconcile_panels(fix_titles=True)

    after = _settings.get("panels") or {}
    assert after[p_tpl["instanceId"]]["title"] == "Pad Panel B"
    assert after[p_custom["instanceId"]]["title"] == "My Cool Knobs"
    assert any("retitled" in msg for msg in result["fixed"])


def test_reconcile_is_idempotent(monkeypatch, tmp_path):
    """Running reconcile twice produces the same state on the second call."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)

    # Seed a stale ref + empty slot so there's something to fix.
    p = core.create_panel("pad", bank="A", preset="Main", activate=False)
    active = dict(_settings.get("active_panels") or {})
    active["knob:A"] = "ghost-knob"
    _settings.put("active_panels", active)

    r1 = core.reconcile_panels()
    state_after_first = dict(_settings.get("active_panels") or {})

    r2 = core.reconcile_panels()
    state_after_second = dict(_settings.get("active_panels") or {})

    assert state_after_first == state_after_second
    # Second call has nothing to fix.
    assert r2["fixed"] == []


def test_reconcile_does_nothing_on_healthy_state(monkeypatch, tmp_path):
    """Healthy state: no changes, empty fixed/warnings."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)
    core.create_panel("pad", bank="A", preset="Main", activate=True)
    core.create_panel("knob", bank="A", preset="Master", activate=True)

    before_panels = dict(_settings.get("panels") or {})
    before_active = dict(_settings.get("active_panels") or {})

    result = core.reconcile_panels()

    assert result["fixed"] == []
    assert result["warnings"] == []
    assert dict(_settings.get("panels") or {}) == before_panels
    assert dict(_settings.get("active_panels") or {}) == before_active


def test_reconcile_warns_on_ambiguous_slot(monkeypatch, tmp_path):
    """If multiple panels match an empty slot, we should warn and not pick."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)
    core.create_panel("pad", bank="A", preset="Main", activate=False)
    core.create_panel("pad", bank="A", preset="Main", activate=False)

    result = core.reconcile_panels()

    after = _settings.get("active_panels") or {}
    assert not after.get("pad:A")
    assert any("candidate" in msg for msg in result["warnings"])
