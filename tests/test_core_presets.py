"""Tests for CRUD operations with presets and validation in AppCore/Mapper."""

import pytest
import toml

from mapper import (
    ActionDef,
    AppConfig,
    KnobMapping,
    Mapper,
    PadMapping,
    PadPreset,
    load_config,
    save_config,
)


# ------------------------------------------------------------------
# save_config round-trip
# ------------------------------------------------------------------

def test_save_config_roundtrip(tmp_path):
    """save_config -> load_config should preserve all data."""
    cfg = AppConfig(device_name="TestDev")
    cfg.knobs = [
        KnobMapping(cc=48, label="Vol", action=ActionDef(type="volume", target="master")),
    ]
    cfg.pad_presets = [
        PadPreset(name="ModeA", pads=[
            PadMapping(note=16, label="Play", action=ActionDef(type="keystroke", keys="space"), hotkey="F1"),
            PadMapping(note=17, label="Stop", action=ActionDef(type="shell", command="echo hi")),
        ]),
        PadPreset(name="ModeB", pads=[]),
    ]
    p = tmp_path / "config.toml"
    save_config(cfg, p)

    loaded = load_config(p)
    assert loaded.device_name == "TestDev"
    assert len(loaded.pad_presets) == 2
    assert loaded.pad_presets[0].name == "ModeA"
    assert loaded.pad_presets[0].pads[0].label == "Play"
    assert loaded.pad_presets[0].pads[0].hotkey == "F1"
    assert loaded.pad_presets[0].pads[0].action.keys == "space"
    assert loaded.pad_presets[0].pads[1].action.command == "echo hi"
    assert loaded.pad_presets[1].name == "ModeB"
    assert len(loaded.pad_presets[1].pads) == 0
    assert len(loaded.knobs) == 1
    assert loaded.knobs[0].action.target == "master"


# ------------------------------------------------------------------
# Mapper public API
# ------------------------------------------------------------------

def _make_mapper(*preset_names):
    cfg = AppConfig()
    cfg.pad_presets = [PadPreset(name=n) for n in preset_names]
    return Mapper(cfg)


def test_rebuild_maps_public():
    m = _make_mapper("A", "B")
    # Should work without error
    m.rebuild_maps()


def test_get_set_current_preset_index():
    m = _make_mapper("A", "B", "C")
    assert m.get_current_preset_index() == 0
    m.set_current_preset_index(2)
    assert m.get_current_preset_index() == 2
    # Clamp high
    m.set_current_preset_index(100)
    assert m.get_current_preset_index() == 2
    # Clamp low
    m.set_current_preset_index(-5)
    assert m.get_current_preset_index() == 0


# ------------------------------------------------------------------
# Routing table preserves other bank
# ------------------------------------------------------------------

def test_routing_preserves_other_bank():
    """Setting bankA routing should not affect bankB pads."""
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(name="Main", pads=[
            PadMapping(note=16, label="A1", action=ActionDef(type="keystroke", keys="a")),
            PadMapping(note=24, label="B1", action=ActionDef(type="keystroke", keys="b")),
        ]),
        PadPreset(name="Alt", pads=[
            PadMapping(note=16, label="Alt-A1", action=ActionDef(type="shell", command="ls")),
        ]),
    ]
    m = Mapper(cfg)
    m.set_midi_routing("bankA", "Alt")
    m.set_midi_routing("bankB", "Main")

    # Bank A pad should come from Alt preset
    pad_a = m.lookup_pad(16)
    assert pad_a is not None
    assert pad_a.label == "Alt-A1"

    # Bank B pad should come from Main preset
    pad_b = m.lookup_pad(24)
    assert pad_b is not None
    assert pad_b.label == "B1"


# ------------------------------------------------------------------
# Preset name validation
# ------------------------------------------------------------------

def test_validate_preset_name_valid():
    from backend.core import _validate_preset_name
    assert _validate_preset_name("My Preset") is None
    assert _validate_preset_name("test-123") is None
    assert _validate_preset_name("OBS (main)") is None


def test_validate_preset_name_empty():
    from backend.core import _validate_preset_name
    assert _validate_preset_name("") is not None
    assert _validate_preset_name("   ") is not None


def test_validate_preset_name_too_long():
    from backend.core import _validate_preset_name
    assert _validate_preset_name("x" * 51) is not None
    assert _validate_preset_name("x" * 50) is None


def test_validate_preset_name_control_chars():
    from backend.core import _validate_preset_name
    assert _validate_preset_name("foo\x00bar") is not None
    assert _validate_preset_name("foo\nbar") is not None


# ------------------------------------------------------------------
# Params validation
# ------------------------------------------------------------------

def test_validate_params_valid():
    from backend.core import _validate_params
    assert _validate_params({}) is None
    assert _validate_params({"band": 0, "freq": 100.0, "name": "test"}) is None


def test_validate_params_nested():
    from backend.core import _validate_params
    assert _validate_params({"nested": {"a": 1}}) is not None
    assert _validate_params({"list": [1, 2]}) is not None


def test_validate_params_too_many_keys():
    from backend.core import _validate_params
    big = {f"k{i}": i for i in range(21)}
    assert _validate_params(big) is not None
    ok = {f"k{i}": i for i in range(20)}
    assert _validate_params(ok) is None


def test_validate_params_bad_type():
    from backend.core import _validate_params
    assert _validate_params({"k": object()}) is not None
    assert _validate_params("not a dict") is not None


# ------------------------------------------------------------------
# Panel ID validation
# ------------------------------------------------------------------

def test_validate_panel_id():
    """Legacy panel IDs are kept for reference but validation now accepts any panel ID."""
    from backend.core import _LEGACY_PANEL_IDS
    assert "bankA" in _LEGACY_PANEL_IDS
    assert "bankB" in _LEGACY_PANEL_IDS
    assert "knobs" in _LEGACY_PANEL_IDS


# ------------------------------------------------------------------
# Registry composite keys
# ------------------------------------------------------------------

def test_registry_composite_keys():
    """Registry should store pads under composite keys preset:note."""
    from pad_registry import PadRegistry, PadEntry, pad_key
    reg = PadRegistry()
    reg.set_pad("Spotify", 16, PadEntry(note=16, label="Play"))
    reg.set_pad("OBS", 16, PadEntry(note=16, label="Record"))

    p1 = reg.get_pad("Spotify", 16)
    p2 = reg.get_pad("OBS", 16)
    assert p1 is not None
    assert p1.label == "Play"
    assert p1.preset == "Spotify"
    assert p2 is not None
    assert p2.label == "Record"
    assert p2.preset == "OBS"


def test_registry_get_preset_pads():
    from pad_registry import PadRegistry, PadEntry
    reg = PadRegistry()
    reg.set_pad("X", 16, PadEntry(note=16, label="A"))
    reg.set_pad("X", 17, PadEntry(note=17, label="B"))
    reg.set_pad("Y", 16, PadEntry(note=16, label="C"))

    x_pads = reg.get_preset_pads("X")
    assert len(x_pads) == 2
    assert x_pads[16].label == "A"
    assert x_pads[17].label == "B"

    y_pads = reg.get_preset_pads("Y")
    assert len(y_pads) == 1
    assert y_pads[16].label == "C"


def test_knob_panel_dispatch_follows_active_pad_bank(monkeypatch, tmp_path):
    """When active pad bank is A -> CC48 resolved via knobBank-A; when B -> via knobBank-B."""
    import settings as _settings
    from mapper import KnobPreset

    # Isolate settings file
    monkeypatch.setattr(_settings, "_PATH",
        tmp_path / "settings.json", raising=False)
    _settings._data = {}

    cfg = AppConfig()
    cfg.pad_presets = [PadPreset(name="Default")]
    cfg.knob_presets = [
        KnobPreset(name="Master", knobs=[
            KnobMapping(cc=48, label="Vol",
                action=ActionDef(type="volume", target="master")),
        ]),
        KnobPreset(name="Piano FX", knobs=[
            KnobMapping(cc=48, label="Delay",
                action=ActionDef(type="plugin", target="Piano:delay")),
        ]),
    ]
    m = Mapper(cfg)
    m.set_knob_routing("knobBank-A", "Master")
    m.set_knob_routing("knobBank-B", "Piano FX")

    # Active bank -> knob panel resolution (mimics AppCore._active_knob_panel)
    def resolve(active_bank: str) -> str:
        return "knobBank-B" if active_bank == "B" else "knobBank-A"

    # When bank A is active -> Master
    k = m.lookup_knob_for_panel(resolve("A"), 48)
    assert k is not None
    assert k.label == "Vol"
    assert k.action.target == "master"

    # When bank B is active -> Piano FX
    k = m.lookup_knob_for_panel(resolve("B"), 48)
    assert k is not None
    assert k.label == "Delay"
    assert k.action.target == "Piano:delay"


def test_knob_routing_independent_switches():
    """Switching knob preset on one panel does NOT affect the other."""
    from mapper import KnobPreset
    cfg = AppConfig()
    cfg.knob_presets = [
        KnobPreset(name="A1", knobs=[
            KnobMapping(cc=48, label="A1-48",
                action=ActionDef(type="volume", target="master")),
        ]),
        KnobPreset(name="A2", knobs=[
            KnobMapping(cc=48, label="A2-48",
                action=ActionDef(type="volume", target="mic")),
        ]),
        KnobPreset(name="B1", knobs=[
            KnobMapping(cc=48, label="B1-48",
                action=ActionDef(type="plugin", target="Piano:delay")),
        ]),
    ]
    m = Mapper(cfg)
    m.set_knob_routing("knobBank-A", "A1")
    m.set_knob_routing("knobBank-B", "B1")

    # Switch knobBank-A to A2 — should not change B
    m.set_knob_routing("knobBank-A", "A2")
    kA = m.lookup_knob_for_panel("knobBank-A", 48)
    kB = m.lookup_knob_for_panel("knobBank-B", 48)
    assert kA.label == "A2-48"
    assert kB.label == "B1-48"


def test_set_knob_routing_does_not_mutate_global_knobs():
    """Activating a knob preset per-panel must NOT mutate config.knobs
    (legacy global), which would break independence of A/B routing."""
    from mapper import KnobPreset
    legacy = [
        KnobMapping(cc=48, label="Legacy",
            action=ActionDef(type="volume", target="master")),
    ]
    cfg = AppConfig()
    cfg.knobs = list(legacy)
    cfg.knob_presets = [
        KnobPreset(name="Alt", knobs=[
            KnobMapping(cc=48, label="Alt-48",
                action=ActionDef(type="plugin", target="Piano:delay")),
        ]),
    ]
    m = Mapper(cfg)
    assert m.set_knob_routing("knobBank-B", "Alt") is True
    # config.knobs must be untouched
    assert len(cfg.knobs) == len(legacy)
    assert cfg.knobs[0].label == "Legacy"
    assert cfg.knobs[0].action.target == "master"


def test_switch_knob_preset_does_not_save_config_toml(monkeypatch, tmp_path):
    """switch_knob_preset must not write config.toml on each click."""
    import settings as _settings
    import backend.core as core_mod
    from mapper import KnobPreset

    # Isolate settings to tmp
    monkeypatch.setattr(_settings, "_PATH",
        tmp_path / "settings.json", raising=False)
    _settings._data = {}

    calls = {"n": 0}
    def fake_save_config(*args, **kwargs):
        calls["n"] += 1
    monkeypatch.setattr(core_mod, "save_config", fake_save_config)

    core = core_mod.AppCore.__new__(core_mod.AppCore)
    core._lock = __import__("threading").RLock()
    cfg = AppConfig()
    cfg.pad_presets = [PadPreset(name="Default")]
    cfg.knob_presets = [
        KnobPreset(name="Master", knobs=[
            KnobMapping(cc=48, label="Vol",
                action=ActionDef(type="volume", target="master")),
        ]),
    ]
    core.config = cfg
    core.mapper = Mapper(cfg)
    # stub event_bus / logger
    class _Bus:
        def publish(self, *a, **kw): pass
    core.event_bus = _Bus()
    core.log_buffer = []
    core._LOG_BUFFER_MAX = 200

    ok = core.switch_knob_preset("Master", "knobBank-A")
    assert ok is True
    assert calls["n"] == 0, "save_config must NOT be called from switch_knob_preset"


def test_registry_get_all_hotkeys():
    from pad_registry import PadRegistry, PadEntry
    reg = PadRegistry()
    reg.set_pad("A", 16, PadEntry(note=16, label="X", hotkey="F1"))
    reg.set_pad("B", 16, PadEntry(note=16, label="Y", hotkey="F2"))
    reg.set_pad("A", 17, PadEntry(note=17, label="Z"))  # no hotkey

    hotkeys = reg.get_all_hotkeys()
    assert len(hotkeys) == 2
    presets = {(p, n) for p, n, h in hotkeys}
    assert ("A", 16) in presets
    assert ("B", 16) in presets
