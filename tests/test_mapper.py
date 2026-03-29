

import pytest

from mapper import (
    ActionDef,
    AppConfig,
    KnobMapping,
    Mapper,
    Mode,
    PadMapping,
    load_config,
)


MINIMAL_TOML = """
[device]
name = "Test Device"

[[modes]]
name = "ModeA"
color = "#112233"
icon = "a"

[[modes.pads]]
note = 20
label = "Pad1"
[modes.pads.action]
type = "keys"
keys = "ctrl+a"

[[modes]]
name = "ModeB"
color = "#445566"

[[modes.pads]]
note = 18
label = "Other"
[modes.pads.action]
type = "command"
command = "echo hi"

[[knobs]]
cc = 10
label = "Vol"
[knobs.action]
type = "keys"
keys = "v"

[[contexts]]
process = "app.exe"
mode = "ModeA"
"""


def test_load_config_parses_valid_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL_TOML, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.device_name == "Test Device"
    assert len(cfg.modes) == 2
    assert cfg.modes[0].name == "ModeA"
    assert cfg.modes[0].pads[0].note == 20
    assert cfg.modes[0].pads[0].action.type == "keys"
    assert len(cfg.knobs) == 1
    assert cfg.knobs[0].cc == 10
    assert len(cfg.contexts) == 1
    assert cfg.contexts[0].mode == "ModeA"


def test_load_config_missing_file(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_lookup_pad_mapped():
    cfg = AppConfig()
    cfg.modes = [
        Mode(
            name="m1",
            color="#000",
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
    cfg.modes = [
        Mode(
            name="m1",
            color="#000",
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


def test_set_mode_changes_index():
    cfg = AppConfig()
    cfg.modes = [
        Mode(name="a", color="#000"),
        Mode(name="b", color="#111"),
    ]
    m = Mapper(cfg)
    m.set_mode(1)
    assert m.current_mode.name == "b"


def test_set_mode_clamps():
    cfg = AppConfig()
    cfg.modes = [
        Mode(name="a", color="#000"),
        Mode(name="b", color="#111"),
    ]
    m = Mapper(cfg)
    m.set_mode(-100)
    assert m.current_mode_index == 0
    m.set_mode(100)
    assert m.current_mode_index == 1


def test_set_mode_by_name_case_insensitive():
    cfg = AppConfig()
    cfg.modes = [
        Mode(name="Alpha", color="#000"),
        Mode(name="Beta", color="#111"),
    ]
    m = Mapper(cfg)
    ok = m.set_mode_by_name("beta")
    assert ok is True
    assert m.current_mode.name == "Beta"
    assert m.set_mode_by_name("ALPHA") is True
    assert m.current_mode.name == "Alpha"
    assert m.set_mode_by_name("missing") is False
