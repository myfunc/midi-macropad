"""Tests for the freeform panels model (pad/knob panels)."""
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

    # Redirect settings to an isolated file & fresh state
    monkeypatch.setattr(_settings, "_PATH",
        tmp_path / "settings.json", raising=False)
    _settings._data = {}

    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(name="Main", pads=[
            PadMapping(note=16, label="MainA16",
                action=ActionDef(type="keystroke", keys="a")),
            PadMapping(note=24, label="MainB24",
                action=ActionDef(type="keystroke", keys="b")),
        ]),
        PadPreset(name="Alt", pads=[
            PadMapping(note=16, label="AltA16",
                action=ActionDef(type="shell", command="ls")),
        ]),
    ]
    cfg.knob_presets = [
        KnobPreset(name="Master", knobs=[
            KnobMapping(cc=48, label="Vol",
                action=ActionDef(type="volume", target="master")),
        ]),
        KnobPreset(name="FX", knobs=[
            KnobMapping(cc=48, label="Delay",
                action=ActionDef(type="plugin", target="Piano:delay")),
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


# ------------------------------------------------------------------
# create / activate / delete
# ------------------------------------------------------------------

def test_create_panel_returns_unique_id(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    a = core.create_panel("pad", bank="A", preset="Main")
    b = core.create_panel("pad", bank="A", preset="Main")
    assert a["instanceId"] != b["instanceId"]
    assert a["type"] == "pad" and a["bank"] == "A"
    assert a["preset"] == "Main"
    panels = core.list_panels()
    assert a["instanceId"] in panels
    assert b["instanceId"] in panels


def test_activate_panel_deactivates_same_type_and_bank(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    p1 = core.create_panel("pad", bank="A", preset="Main")
    p2 = core.create_panel("pad", bank="A", preset="Alt")
    core.activate_panel(p1["instanceId"])
    assert core.mapper.get_active_panel("pad", "A") == p1["instanceId"]
    core.activate_panel(p2["instanceId"])
    assert core.mapper.get_active_panel("pad", "A") == p2["instanceId"]
    # Previous was implicitly deactivated — only one active for (pad, A)
    active = {v for v in core.mapper.get_all_active_panels().values()}
    assert p1["instanceId"] not in active


def test_pad_A_and_pad_B_can_both_be_active(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    pa = core.create_panel("pad", bank="A", preset="Main", activate=True)
    pb = core.create_panel("pad", bank="B", preset="Main", activate=True)
    assert core.mapper.get_active_panel("pad", "A") == pa["instanceId"]
    assert core.mapper.get_active_panel("pad", "B") == pb["instanceId"]


def test_delete_active_panel_clears_active(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    p = core.create_panel("pad", bank="A", preset="Main", activate=True)
    assert core.mapper.get_active_panel("pad", "A") == p["instanceId"]
    assert core.delete_panel(p["instanceId"]) is True
    assert core.mapper.get_active_panel("pad", "A") is None
    assert p["instanceId"] not in core.list_panels()


# ------------------------------------------------------------------
# Dispatch via active panel
# ------------------------------------------------------------------

def test_lookup_pad_resolves_through_active_panel_preset(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    core.create_panel("pad", bank="A", preset="Main", activate=True)
    # A-active preset Main -> note 16 = "MainA16"
    pad = core.mapper.lookup_pad_for_active(16)
    assert pad is not None and pad.label == "MainA16"

    # Create another panel with preset Alt and activate it -> takes over
    p2 = core.create_panel("pad", bank="A", preset="Alt")
    core.activate_panel(p2["instanceId"])
    pad = core.mapper.lookup_pad_for_active(16)
    assert pad is not None and pad.label == "AltA16"


def test_inactive_panel_does_not_dispatch(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    # Two panels on (pad, A): one active (Main), one inactive (Alt)
    active = core.create_panel("pad", bank="A", preset="Main", activate=True)
    inactive = core.create_panel("pad", bank="A", preset="Alt")
    # Sanity: both tracked
    assert core.mapper.get_panel_preset(active["instanceId"]) == "Main"
    assert core.mapper.get_panel_preset(inactive["instanceId"]) == "Alt"
    # Dispatch resolves through Main (active), not Alt
    pad = core.mapper.lookup_pad_for_active(16)
    assert pad is not None and pad.label == "MainA16"


def test_update_inactive_panel_preset_does_not_change_dispatch(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    active = core.create_panel("pad", bank="A", preset="Main", activate=True)
    inactive = core.create_panel("pad", bank="A", preset="Main")
    # Change the inactive panel's preset to Alt — active panel still Main
    core.update_panel(inactive["instanceId"], preset="Alt")
    pad = core.mapper.lookup_pad_for_active(16)
    assert pad is not None and pad.label == "MainA16"


def test_update_panel_bank_change_from_active_clears_old_slot(monkeypatch, tmp_path):
    """Changing bank on an active panel must vacate the old (type, bank) slot."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)
    p = core.create_panel("pad", bank="A", preset="Main", activate=True)
    # Occupy new target bank B with another active panel so auto-activation
    # on the new slot is prevented.
    other = core.create_panel("pad", bank="B", preset="Alt", activate=True)
    assert core.mapper.get_active_panel("pad", "A") == p["instanceId"]
    assert core.mapper.get_active_panel("pad", "B") == other["instanceId"]

    core.update_panel(p["instanceId"], bank="B")

    active = _settings.get("active_panels") or {}
    # Old slot pad:A is cleared (either None or missing)
    assert not active.get("pad:A")
    # New slot pad:B remains held by the other panel (no override)
    assert active.get("pad:B") == other["instanceId"]
    # Mapper routing for (pad, A) is cleared
    assert core.mapper.get_active_panel("pad", "A") is None
    # Panel p is not active on either bank now
    mapper_active_values = set(core.mapper.get_all_active_panels().values())
    assert p["instanceId"] not in mapper_active_values


def test_update_panel_bank_change_auto_activates_on_empty_new_slot(monkeypatch, tmp_path):
    """If the new bank slot is empty, moving an active panel auto-activates on it."""
    import settings as _settings
    core = _fresh_core(monkeypatch, tmp_path)
    p = core.create_panel("pad", bank="A", preset="Main", activate=True)
    assert core.mapper.get_active_panel("pad", "A") == p["instanceId"]
    # No panel on bank B yet
    assert core.mapper.get_active_panel("pad", "B") is None

    core.update_panel(p["instanceId"], bank="B")

    active = _settings.get("active_panels") or {}
    assert not active.get("pad:A")
    assert active.get("pad:B") == p["instanceId"]
    assert core.mapper.get_active_panel("pad", "A") is None
    assert core.mapper.get_active_panel("pad", "B") == p["instanceId"]


def test_update_active_panel_preset_changes_dispatch(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    p = core.create_panel("pad", bank="A", preset="Main", activate=True)
    core.update_panel(p["instanceId"], preset="Alt")
    pad = core.mapper.lookup_pad_for_active(16)
    assert pad is not None and pad.label == "AltA16"


def test_knob_dispatch_follows_active_knob_panel(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    core.create_panel("knob", bank="A", preset="Master", activate=True)
    core.create_panel("knob", bank="B", preset="FX", activate=True)

    k = core.mapper.lookup_knob_for_active_bank("A", 48)
    assert k is not None and k.label == "Vol"
    k = core.mapper.lookup_knob_for_active_bank("B", 48)
    assert k is not None and k.label == "Delay"


# ------------------------------------------------------------------
# Migration
# ------------------------------------------------------------------

def test_migrate_legacy_panel_presets(monkeypatch, tmp_path):
    """Legacy panel_presets are converted to 4 freeform panels + layout reset."""
    import settings as _settings
    import backend.core as core_mod

    monkeypatch.setattr(_settings, "_PATH",
        tmp_path / "settings.json", raising=False)
    _settings._data = {
        "panel_presets": {
            "bankA": {"preset": "Main"},
            "bankB": {"preset": "Alt"},
            "knobBank-A": {"preset": "Master"},
            "knobBank-B": {"preset": "FX"},
        },
        "ui_layout": {"some": "layout"},
    }

    core = _fresh_core(monkeypatch, tmp_path)
    # _fresh_core reset _data to {} — restore legacy for this test
    _settings._data = {
        "panel_presets": {
            "bankA": {"preset": "Main"},
            "bankB": {"preset": "Alt"},
            "knobBank-A": {"preset": "Master"},
            "knobBank-B": {"preset": "FX"},
        },
        "ui_layout": {"some": "layout"},
    }

    core._migrate_panel_presets()

    panels = _settings.get("panels") or {}
    active = _settings.get("active_panels") or {}
    # Exactly 4 panels created
    assert len(panels) == 4
    # Active slots for all four (type, bank) pairs
    assert set(active.keys()) == {"pad:A", "pad:B", "knob:A", "knob:B"}
    # Presets preserved
    types_banks_presets = {(p["type"], p["bank"]): p["preset"]
        for p in panels.values()}
    assert types_banks_presets[("pad", "A")] == "Main"
    assert types_banks_presets[("pad", "B")] == "Alt"
    assert types_banks_presets[("knob", "A")] == "Master"
    assert types_banks_presets[("knob", "B")] == "FX"
    # Layout reset
    assert _settings.get("ui_layout") is None


def test_migrate_is_idempotent(monkeypatch, tmp_path):
    """Calling migration twice must not create duplicate starter panels."""
    core = _fresh_core(monkeypatch, tmp_path)
    core._migrate_panel_presets()
    import settings as _settings
    first = dict(_settings.get("panels") or {})
    core._migrate_panel_presets()
    second = dict(_settings.get("panels") or {})
    assert first == second


# ------------------------------------------------------------------
# Physical pad press still tracks the bank; dispatch honors active panel
# ------------------------------------------------------------------

def test_pad_press_dispatch_uses_active_panel(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    core.create_panel("pad", bank="A", preset="Alt", activate=True)
    core.create_panel("pad", bank="B", preset="Main", activate=True)

    # Press bank-A note -> Alt preset
    core._handle_pad_press(16, 100)
    assert core._active_pad_bank_mem == "A"
    # Press bank-B note -> Main preset
    core._handle_pad_press(24, 100)
    assert core._active_pad_bank_mem == "B"

    # lookup via active
    assert core.mapper.lookup_pad_for_active(16).label == "AltA16"
    assert core.mapper.lookup_pad_for_active(24).label == "MainB24"
