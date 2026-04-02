
import pytest

from mapper import (
    ActionDef,
    AppConfig,
    KnobMapping,
    Mapper,
    PadMapping,
    PadPreset,
    load_config,
)


MINIMAL_TOML = """
[device]
name = "Test Device"

[[pad_presets]]
name = "ModeA"

[[pad_presets.pads]]
note = 20
label = "Pad1"
[pad_presets.pads.action]
type = "keys"
keys = "ctrl+a"

[[pad_presets]]
name = "ModeB"

[[pad_presets.pads]]
note = 18
label = "Other"
[pad_presets.pads.action]
type = "command"
command = "echo hi"

[[knobs]]
cc = 10
label = "Vol"
[knobs.action]
type = "keys"
keys = "v"
"""


def test_load_config_parses_valid_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL_TOML, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.device_name == "Test Device"
    assert len(cfg.pad_presets) == 2
    assert cfg.pad_presets[0].name == "ModeA"
    assert cfg.pad_presets[0].pads[0].note == 20
    assert cfg.pad_presets[0].pads[0].action.type == "keys"
    assert len(cfg.knobs) == 1
    assert cfg.knobs[0].cc == 10


def test_load_config_missing_file(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_lookup_pad_mapped():
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(
            name="m1",
            pads=[
                PadMapping(
                    note=20,
                    label="L",
                    action=ActionDef(type="keys", keys="a"),
                )
            ],
        )
    ]
    m = Mapper(cfg)
    p = m.lookup_pad(20)
    assert p is not None
    assert p.label == "L"


def test_lookup_pad_unmapped_returns_none():
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(
            name="m1",
            pads=[
                PadMapping(
                    note=20,
                    label="L",
                    action=ActionDef(type="keys"),
                )
            ],
        )
    ]
    m = Mapper(cfg)
    assert m.lookup_pad(99) is None


def test_lookup_knob():
    cfg = AppConfig()
    cfg.knobs = [
        KnobMapping(cc=7, label="K", action=ActionDef(type="keys", keys="k")),
    ]
    m = Mapper(cfg)
    k = m.lookup_knob(7)
    assert k is not None
    assert k.label == "K"
    assert m.lookup_knob(8) is None


def test_set_preset_changes_index():
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(name="a"),
        PadPreset(name="b"),
    ]
    m = Mapper(cfg)
    m.set_preset(1)
    assert m.current_preset.name == "b"


def test_set_preset_clamps():
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(name="a"),
        PadPreset(name="b"),
    ]
    m = Mapper(cfg)
    m.set_preset(-100)
    assert m.current_preset_index == 0
    m.set_preset(100)
    assert m.current_preset_index == 1


def test_set_preset_by_name_case_insensitive():
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(name="Alpha"),
        PadPreset(name="Beta"),
    ]
    m = Mapper(cfg)
    ok = m.set_preset_by_name("beta")
    assert ok is True
    assert m.current_preset.name == "Beta"
    assert m.set_preset_by_name("ALPHA") is True
    assert m.current_preset.name == "Alpha"
    assert m.set_preset_by_name("missing") is False


def test_get_plugin_notes():
    cfg = AppConfig()
    cfg.pad_presets = [
        PadPreset(
            name="p1",
            pads=[
                PadMapping(
                    note=16,
                    label="A",
                    action=ActionDef(type="plugin", target="Foo"),
                ),
                PadMapping(
                    note=17,
                    label="B",
                    action=ActionDef(type="plugin", target="Foo:extra"),
                ),
                PadMapping(
                    note=18,
                    label="C",
                    action=ActionDef(type="keystroke", keys="x"),
                ),
            ],
        ),
    ]
    m = Mapper(cfg)
    assert m.get_plugin_notes("Foo") == {16, 17}
    assert m.get_plugin_notes("Bar") == set()
