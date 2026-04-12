"""Тесты для CRUD-операций с пресетами и валидации в AppCore/Mapper."""

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
# Mapper public API (Fix 3)
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
# apply_partial_preset does not wipe other bank (Fix 4)
# ------------------------------------------------------------------

def test_apply_partial_preset_preserves_other_bank():
    """apply_partial_preset should only change notes in note_filter."""
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
    m.set_preset(0)

    # Apply Alt preset only to bank A notes
    bank_a = [16, 17, 18, 19, 20, 21, 22, 23]
    ok = m.apply_partial_preset("Alt", bank_a)
    assert ok is True

    # Bank B pad should still be in registry
    entry_b = m.registry.get_pad(24)
    assert entry_b is not None
    assert entry_b.label == "B1"

    # Bank A pad should be updated
    entry_a = m.registry.get_pad(16)
    assert entry_a is not None
    assert entry_a.label == "Alt-A1"


# ------------------------------------------------------------------
# Preset name validation (Fix 5)
# ------------------------------------------------------------------

def test_validate_preset_name_valid():
    from backend.core import _validate_preset_name
    assert _validate_preset_name("My Preset") is None
    assert _validate_preset_name("test-123") is None
    assert _validate_preset_name("OBS (main)") is None
    assert _validate_preset_name("Пресет") is None  # Unicode letters


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
# Params validation (Fix 7)
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
# Panel ID validation (Fix 6)
# ------------------------------------------------------------------

def test_validate_panel_id():
    from backend.core import _VALID_PANEL_IDS
    assert "bankA" in _VALID_PANEL_IDS
    assert "bankB" in _VALID_PANEL_IDS
    assert "knobs" in _VALID_PANEL_IDS
    assert "invalid" not in _VALID_PANEL_IDS
