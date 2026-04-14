"""Tests for AppCore.handle_piano_note dispatch (play vs map priority)."""
from __future__ import annotations

import threading

import pytest

from mapper import (
    ActionDef, AppConfig, KnobPreset, Mapper, PadPreset,
    PianoKeyMapping, PianoPreset,
)


def _fresh_core(monkeypatch, tmp_path, *, with_piano_plugin: bool = False):
    import settings as _settings
    import backend.core as core_mod

    monkeypatch.setattr(_settings, "_PATH",
        tmp_path / "settings.json", raising=False)
    _settings._data = {}

    cfg = AppConfig()
    cfg.pad_presets = [PadPreset(name="Default")]
    cfg.piano_presets = [
        PianoPreset(name="MapMain", keys=[
            PianoKeyMapping(note=48, label="Copy",
                action={"type": "keystroke", "keys": "ctrl+c"}),
        ]),
        PianoPreset(name="MapAlt"),
    ]

    core = core_mod.AppCore.__new__(core_mod.AppCore)
    core._lock = threading.RLock()
    core._active_pad_bank_mem = None
    core.log_buffer = []
    core._LOG_BUFFER_MAX = 200
    core.config = cfg
    core.mapper = Mapper(cfg)

    class _Bus:
        def __init__(self):
            self.events: list = []
        def publish(self, name, payload=None):
            self.events.append((name, payload))
    core.event_bus = _Bus()

    class _PianoPlugin:
        def __init__(self):
            self.on_calls: list = []
            self.off_calls: list = []
        def _note_on(self, note, velocity):
            self.on_calls.append((note, velocity))
        def _note_off(self, note):
            self.off_calls.append(note)

    class _PM:
        enabled = []
        def __init__(self, plugins):
            self.plugins = plugins
        def on_pad_press(self, *a, **kw): return False
        def on_pad_release(self, *a, **kw): return False
        def on_knob(self, *a, **kw): return False
        def on_pitch_bend(self, *a, **kw): return None
        def on_mode_changed(self, *a, **kw): return None
        def notify_preset_changed(self, *a, **kw): return None
        def get_all_pad_labels(self): return {}
        def get_pad_labels_by_plugin(self): return {}
        def get_pad_states_by_plugin(self): return {}
        def discover(self): return []

    plugins = {"Piano": _PianoPlugin()} if with_piano_plugin else {}
    core.plugin_manager = _PM(plugins)

    # Capture executor calls.
    core.executed_actions: list = []
    def _fake_execute(action):
        core.executed_actions.append(action)
    core._execute_action = _fake_execute

    class _Obs:
        connected = False; current_scene = ""; is_recording = False
        is_streaming = False; is_replay_buffer_active = False
        scene_names: list = []
    core.obs = _Obs()

    class _Midi:
        connected = False; port_name = ""
    core.midi = _Midi()

    return core


def test_map_bank_dispatches_to_executor(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path)
    # Register a piano MAP panel and activate it.
    p = core.create_panel("piano", bank="map", preset="MapMain", activate=True)
    assert core.mapper.get_active_panel("piano", "map") == p["instanceId"]

    handled = core.handle_piano_note(48, 100, on=True)
    assert handled is True
    assert len(core.executed_actions) == 1
    action = core.executed_actions[0]
    assert action.type == "keystroke"
    assert action.keys == "ctrl+c"


def test_play_bank_dispatches_to_piano_plugin(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path, with_piano_plugin=True)
    p = core.create_panel("piano", bank="play", preset="", activate=True)

    handled_on = core.handle_piano_note(60, 90, on=True)
    handled_off = core.handle_piano_note(60, 0, on=False)
    assert handled_on is True and handled_off is True

    piano = core.plugin_manager.plugins["Piano"]
    assert piano.on_calls == [(60, 90)]
    assert piano.off_calls == [60]
    # Executor not touched.
    assert core.executed_actions == []


def test_map_wins_over_play_when_both_active(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path, with_piano_plugin=True)
    core.create_panel("piano", bank="play", preset="", activate=True)
    core.create_panel("piano", bank="map", preset="MapMain", activate=True)

    handled = core.handle_piano_note(48, 100, on=True)
    assert handled is True
    # Map resolved via executor.
    assert len(core.executed_actions) == 1
    # Piano plugin did NOT receive the note (map priority).
    piano = core.plugin_manager.plugins["Piano"]
    assert piano.on_calls == []


def test_no_active_panel_no_op(monkeypatch, tmp_path):
    core = _fresh_core(monkeypatch, tmp_path, with_piano_plugin=True)
    # No piano panels at all.
    handled = core.handle_piano_note(60, 100, on=True)
    assert handled is False
    assert core.executed_actions == []
    assert core.plugin_manager.plugins["Piano"].on_calls == []


def test_map_bank_outside_range_falls_through(monkeypatch, tmp_path):
    """Notes outside [36,72] while only map is active are not handled (fall through)."""
    core = _fresh_core(monkeypatch, tmp_path)
    core.create_panel("piano", bank="map", preset="MapMain", activate=True)
    # 20 is below the map range.
    handled = core.handle_piano_note(20, 100, on=True)
    assert handled is False
    assert core.executed_actions == []


def test_map_bank_key_without_action_swallows(monkeypatch, tmp_path):
    """In map bank, a note within range but without a mapping is still swallowed."""
    core = _fresh_core(monkeypatch, tmp_path, with_piano_plugin=True)
    core.create_panel("piano", bank="map", preset="MapMain", activate=True)
    # Note 50 is in MapMain's range but not mapped — map swallows (no fallthrough).
    handled = core.handle_piano_note(50, 100, on=True)
    assert handled is True
    assert core.executed_actions == []
    # Piano plugin not called because map had priority.
    assert core.plugin_manager.plugins["Piano"].on_calls == []
