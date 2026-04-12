
import pytest

from mapper import (
    ActionDef,
    AppConfig,
    KnobMapping,
    KnobPreset,
    Mapper,
    PadMapping,
    PadPreset,
    load_config,
    save_config,
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


def test_update_knob_action_writes_toml(tmp_path):
    """update_knob_action should update config.toml and reload knobs."""
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL_TOML, encoding="utf-8")
    cfg = load_config(p)
    m = Mapper(cfg)

    # Knob CC=10 exists in MINIMAL_TOML
    assert m.lookup_knob(10) is not None
    assert m.lookup_knob(10).label == "Vol"

    ok = m.update_knob_action(
        cc=10,
        action_type="plugin",
        target="Voicemeeter:headphones_gain",
        label="Headphones",
        params={},
        config_path=p,
    )
    assert ok is True
    # In-memory config should be updated
    k = m.lookup_knob(10)
    assert k is not None
    assert k.label == "Headphones"
    assert k.action.type == "plugin"
    assert k.action.target == "Voicemeeter:headphones_gain"

    # File should be updated (re-parse)
    import toml
    data = toml.load(str(p))
    knob = data["knobs"][0]
    assert knob["label"] == "Headphones"
    assert knob["action"]["type"] == "plugin"
    assert knob["action"]["target"] == "Voicemeeter:headphones_gain"


def test_update_knob_action_not_found(tmp_path):
    """update_knob_action returns False for unknown CC."""
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL_TOML, encoding="utf-8")
    cfg = load_config(p)
    m = Mapper(cfg)

    ok = m.update_knob_action(
        cc=999, action_type="volume", target="master",
        label="X", params={}, config_path=p,
    )
    assert ok is False


# ── Knob presets ────────────────────────────────────────────────────

KNOB_PRESETS_TOML = """
[device]
name = "Test Device"

[[knobs]]
cc = 48
label = "Vol"
[knobs.action]
type = "volume"
target = "master"

[[knob_presets]]
name = "Master"
[[knob_presets.knobs]]
cc = 48
label = "Volume"
[knob_presets.knobs.action]
type = "volume"
target = "master"

[[knob_presets.knobs]]
cc = 49
label = "Mic"
[knob_presets.knobs.action]
type = "volume"
target = "mic"

[[knob_presets]]
name = "EQ"
[[knob_presets.knobs]]
cc = 48
label = "Low"
[knob_presets.knobs.action]
type = "plugin"
target = "Voicemeeter:eq_band_gain"
[knob_presets.knobs.action.params]
band = 0
freq = 100.0

[[pad_presets]]
name = "Default"
"""


def test_load_config_parses_knob_presets(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(KNOB_PRESETS_TOML, encoding="utf-8")
    cfg = load_config(p)
    assert len(cfg.knob_presets) == 2
    assert cfg.knob_presets[0].name == "Master"
    assert len(cfg.knob_presets[0].knobs) == 2
    assert cfg.knob_presets[0].knobs[0].label == "Volume"
    assert cfg.knob_presets[1].name == "EQ"
    assert cfg.knob_presets[1].knobs[0].action.params["band"] == 0


def test_apply_knob_preset():
    cfg = AppConfig()
    cfg.knobs = [
        KnobMapping(cc=48, label="Vol", action=ActionDef(type="volume", target="master")),
    ]
    cfg.knob_presets = [
        KnobPreset(name="EQ", knobs=[
            KnobMapping(cc=48, label="Low", action=ActionDef(type="plugin", target="VM:eq")),
            KnobMapping(cc=49, label="Mid", action=ActionDef(type="plugin", target="VM:eq")),
        ]),
    ]
    m = Mapper(cfg)
    assert len(m.config.knobs) == 1
    ok = m.apply_knob_preset("EQ")
    assert ok is True
    assert len(m.config.knobs) == 2
    assert m.config.knobs[0].label == "Low"
    assert m.lookup_knob(49) is not None


def test_apply_knob_preset_not_found():
    cfg = AppConfig()
    cfg.knob_presets = []
    m = Mapper(cfg)
    assert m.apply_knob_preset("Missing") is False


def test_apply_knob_preset_case_insensitive():
    cfg = AppConfig()
    cfg.knob_presets = [
        KnobPreset(name="Master", knobs=[
            KnobMapping(cc=48, label="Vol", action=ActionDef(type="volume", target="master")),
        ]),
    ]
    m = Mapper(cfg)
    assert m.apply_knob_preset("master") is True
    assert len(m.config.knobs) == 1


def test_save_config_preserves_knob_presets(tmp_path):
    cfg = AppConfig()
    cfg.knobs = [
        KnobMapping(cc=48, label="Vol", action=ActionDef(type="volume", target="master")),
    ]
    cfg.knob_presets = [
        KnobPreset(name="Master", knobs=[
            KnobMapping(cc=48, label="Volume", action=ActionDef(type="volume", target="master")),
        ]),
        KnobPreset(name="EQ", knobs=[
            KnobMapping(cc=48, label="Low", action=ActionDef(
                type="plugin", target="VM:eq", params={"band": 0, "freq": 100.0},
            )),
        ]),
    ]
    cfg.pad_presets = [PadPreset(name="Default")]
    p = tmp_path / "out.toml"
    save_config(cfg, p)
    # Re-load and verify
    cfg2 = load_config(p)
    assert len(cfg2.knob_presets) == 2
    assert cfg2.knob_presets[0].name == "Master"
    assert cfg2.knob_presets[1].name == "EQ"
    assert cfg2.knob_presets[1].knobs[0].action.params["band"] == 0
