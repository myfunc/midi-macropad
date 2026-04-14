"""Tests for piano preset loading and lookup in Mapper."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mapper import (
    AppConfig, Mapper, PianoKeyMapping, PianoPreset, load_config,
)


def test_load_piano_presets_from_config(tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent("""
        [device]
        name = "Test"

        [[pad_presets]]
        name = "Default"

        [[piano_presets]]
        name = "Main"

        [[piano_presets.keys]]
        note = 48
        label = "Copy"
        [piano_presets.keys.action]
        type = "keystroke"
        keys = "ctrl+c"

        [[piano_presets.keys]]
        note = 50
        label = "Paste"
        [piano_presets.keys.action]
        type = "keystroke"
        keys = "ctrl+v"

        [[piano_presets]]
        name = "Empty"
    """).strip(), encoding="utf-8")

    cfg = load_config(cfg_path)
    assert len(cfg.piano_presets) == 2
    main = cfg.piano_presets[0]
    assert main.name == "Main"
    assert len(main.keys) == 2
    assert main.keys[0].note == 48
    assert main.keys[0].label == "Copy"
    assert main.keys[0].action == {"type": "keystroke", "keys": "ctrl+c"}
    # Empty preset still loads.
    empty = cfg.piano_presets[1]
    assert empty.name == "Empty"
    assert empty.keys == []


def test_lookup_piano_key_returns_mapping():
    cfg = AppConfig()
    cfg.piano_presets = [
        PianoPreset(name="Main", keys=[
            PianoKeyMapping(note=48, label="Copy",
                action={"type": "keystroke", "keys": "ctrl+c"}),
            PianoKeyMapping(note=50, label="Paste",
                action={"type": "keystroke", "keys": "ctrl+v"}),
        ]),
    ]
    m = Mapper(cfg)
    hit = m.lookup_piano_key("Main", 48)
    assert hit is not None and hit.label == "Copy"
    assert hit.action == {"type": "keystroke", "keys": "ctrl+c"}


def test_lookup_missing_key_returns_none():
    cfg = AppConfig()
    cfg.piano_presets = [PianoPreset(name="Main", keys=[])]
    m = Mapper(cfg)
    assert m.lookup_piano_key("Main", 60) is None
    # Non-existent preset returns None too.
    assert m.lookup_piano_key("DoesNotExist", 48) is None


def test_piano_preset_case_insensitive():
    cfg = AppConfig()
    cfg.piano_presets = [
        PianoPreset(name="Main", keys=[PianoKeyMapping(note=48)]),
    ]
    m = Mapper(cfg)
    assert m.get_piano_preset("MAIN") is not None
    assert m.get_piano_preset("main") is not None
