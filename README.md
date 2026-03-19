# MIDI Macropad

Turn an **Akai MPK Mini Play** (or any MIDI controller) into a programmable macro pad for Windows — with a dark IDE-style GUI, plugin system, and voice-to-text translation.

Built with Python, [DearPyGui](https://github.com/hoffstadt/DearPyGui), and [mido](https://mido.readthedocs.io/).

## Features

- **6 switchable modes** — Productivity, Development, Media, OBS, Voice Scribe, Sound Pads
- **8 velocity-sensitive pads** + 4 knobs + joystick, fully configurable via TOML
- **Auto mode switching** — detects the active window and changes mode (e.g. OBS mode when OBS is focused)
- **System volume & mic control** — knobs map to Windows master/mic volume (pycaw)
- **OBS Studio integration** — recording, streaming, scene switching via WebSocket
- **Plugin system** — drop a folder into `plugins/`, get MIDI routing, custom UI tabs, and pad labels
- **Voice Scribe plugin** — speak Russian, paste English with customizable style (OpenAI Whisper + GPT)
- **Sample Player plugin** — polyphonic audio sampler with velocity, pack selector, and volume knob
- **Dark 3-panel UI** — left sidebar, center tabs (pads + log + mixer), right properties panel

## Quick Start

**Requirements:** Windows 10/11, Python 3.11+

```bash
git clone https://github.com/myfunc/midi-macropad.git
cd midi-macropad

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

Or double-click **`MIDI Macropad.bat`** after initial setup.

Connect your MPK Mini Play via USB — the app detects it automatically.

## Hardware — Akai MPK Mini Play MIDI Map

| Control       | MIDI          | Notes                          |
|---------------|---------------|--------------------------------|
| Pads 1–4      | Notes 16–19   | Bottom row, velocity-sensitive |
| Pads 5–8      | Notes 20–23   | Top row, velocity-sensitive    |
| Knobs 1–4     | CC 48–51      | Absolute pots (0–127)          |
| Joystick L/R  | Pitch Bend    | -8192 to +8191                 |
| Joystick U/D  | CC 16         | 0–127                          |
| Keys          | Notes 48–72   | 2 octaves                      |

> Any MIDI controller works — just update `[device] name` and note/CC numbers in `config.toml`.

## Modes

All modes are defined in `config.toml`. Each pad maps to an action (keystroke, shell command, OBS control, plugin handler, etc.).

| Mode           | Color   | Example Pads                                          |
|----------------|---------|-------------------------------------------------------|
| Productivity   | Blue    | Copy, Paste, Undo, Redo, Select All, Cut              |
| Development    | Green   | Terminal, Cmd Palette, Go to File, Comment, Format     |
| Media          | Purple  | Play/Pause, Prev/Next Track, Screenshot, Lock, Desktop|
| OBS            | Red     | Rec, Stream, Scene switch, Mute Mic/Desktop            |
| Voice Scribe   | Pink    | Professional, Casual, Technical, Email, Summary, Raw   |
| Sound Pads     | Yellow  | Managed by Sample Player plugin                        |

## Architecture

```
main.py ─────────────── entry point, wires everything
├── midi_listener.py ── background MIDI thread (mido + rtmidi)
├── mapper.py ───────── TOML config, modes, event → action
├── executor.py ─────── keystrokes (pynput), shell, launch, scroll
├── audio.py ────────── Windows volume + mic (pycaw)
├── app_detector.py ─── Win32 foreground window detection
├── obs_controller.py ─ OBS Studio WebSocket
├── plugins/
│   ├── base.py ─────── abstract Plugin class
│   ├── manager.py ──── discovery, loading, event routing
│   ├── sample_player/  polyphonic WAV sampler
│   └── voice_scribe/   speech-to-translated-text
└── ui/
    ├── dashboard.py ── 3-panel layout, DWM dark theme
    ├── pad_grid.py ─── 2×4 pad buttons with flash
    ├── volume_panel.py  master + mic sliders
    ├── midi_log.py ─── live event log
    ├── sidebar_left.py  mode switcher, plugins
    ├── sidebar_right.py context properties panel
    ├── pad_editor.py ── pad action editor
    └── status_bar.py ── tooltip hover bar
```

## Plugin System

### How it works

1. `PluginManager` scans `plugins/` on startup for folders with `plugin.toml`
2. Each plugin receives MIDI events **before** the default handler
3. Returning `True` from any event hook consumes the event
4. Plugins can register UI tabs, property panels, and custom pad labels

### Plugin interface (`plugins/base.py`)

```python
class Plugin(ABC):
    def on_load(self, config: dict) -> None: ...
    def on_unload(self) -> None: ...
    def on_pad_press(self, note: int, velocity: int) -> bool: ...
    def on_pad_release(self, note: int) -> bool: ...
    def on_knob(self, cc: int, value: int) -> bool: ...
    def on_pitch_bend(self, value: int) -> bool: ...
    def on_mode_changed(self, mode_name: str) -> None: ...
    def get_pad_labels(self) -> dict[int, str]: ...
    def get_status(self) -> tuple[str, tuple] | None: ...
    def build_ui(self, parent_tag: str) -> None: ...
    def register_windows(self) -> list[dict]: ...     # center tab
    def build_window(self, window_id, parent) -> None: ...
    def build_properties(self, parent_tag) -> None: ... # right panel
```

### Manifest (`plugin.toml`)

```toml
[plugin]
name = "My Plugin"
version = "1.0.0"
description = "Does something cool"
entry = "my_module.MyPluginClass"

[settings]
some_option = "default_value"
```

### Voice Scribe

Speak Russian → get English text typed at your cursor with a configurable style.

**Pipeline:** Mic → OpenAI Whisper (RU transcription) → GPT (translation + style) → Ctrl+V

| Pad | Function      | Description                                  |
|-----|---------------|----------------------------------------------|
| 1   | Professional  | Business English (Slack, Teams, email)        |
| 2   | Casual        | Conversational English                        |
| 3   | Technical     | Technical writing (code review, docs)         |
| 4   | Email         | Full email format with greeting and sign-off  |
| 5   | Summary       | Translate + compress into key points          |
| 6   | Raw           | Russian transcription only (no translation)   |
| 7   | Clipboard     | Translate clipboard text (no recording)       |
| 8   | Cancel        | Cancel current recording                      |

Edit style prompts in `plugins/voice_scribe/prompts.toml` or through the Prompt Editor tab in the UI.

**Requires:** `OPENAI_API_KEY` environment variable, or save the key through the plugin settings panel.

### Sample Player

Polyphonic WAV sampler — load packs, play samples on pads with velocity sensitivity.

- Knob 3 (CC 50) controls plugin volume
- Create packs via [Sound Alchemy](https://github.com/myfunc) export or manually as folders with `pack.toml` + WAV files

## Configuration

### `config.toml` — modes, pads, knobs, contexts

```toml
[device]
name = "MPK mini play"

[[modes]]
name = "Productivity"
color = "#3B82F6"

[[modes.pads]]
note = 16
label = "Copy"
action = { type = "keystroke", keys = "ctrl+c" }

[[knobs]]
cc = 48
label = "Volume"
action = { type = "volume", target = "master" }

[[contexts]]
process = "obs64.exe"
mode = "OBS"
```

### Action types

| Type        | Config                           | What it does              |
|-------------|----------------------------------|---------------------------|
| `keystroke` | `keys = "ctrl+c"`               | Emulates key combination  |
| `volume`    | `target = "master"` or `"mic"`  | Windows volume control    |
| `scroll`    | —                                | Mouse scroll              |
| `shell`     | `command = "..."`                | Shell command             |
| `launch`    | `command = "..."`                | Launch a program          |
| `obs`       | `target = "toggle_recording"`   | OBS WebSocket command     |

## Dependencies

```
mido, python-rtmidi, pynput, pycaw, dearpygui, comtypes,
toml, obs-websocket-py, sounddevice, soundfile, numpy, openai
```

## License

[MIT](LICENSE)
