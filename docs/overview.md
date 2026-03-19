# Project Overview

`MIDI Macropad` turns a MIDI controller into a programmable Windows control surface with a desktop UI, app-aware modes, and plugin-driven behavior.

It started as a way to make repetitive computer actions feel more physical and more immediate. Instead of reaching for scattered shortcuts, menus, and tiny UI buttons, the project puts common workflows onto pads, knobs, and mode switches that are fast to reach and easy to remember.

## Core Idea

The project combines three things in one tool:

- **Hardware control** for shortcuts, launching apps, scrolling, and media actions.
- **Context-aware switching** so the controller can adapt to the active app.
- **Plugin-based workflows** for features that deserve their own logic and UI.

## Feature Snapshot

- 6 built-in modes: `Productivity`, `Development`, `Media`, `OBS`, `Voice Scribe`, `Sound Pads`
- 8 velocity-sensitive pads, 4 knobs, and joystick support
- TOML-based configuration for modes, pads, knobs, and app contexts
- Automatic mode switching based on the foreground application
- Windows master and mic volume control
- OBS Studio integration over WebSocket
- Shared runtime feedback service that routes user-facing cues to MIDI out
- Device-side musical feedback phrases tuned for `MPK Mini Play`
- Plugin loading from the `plugins/` folder
- Dark three-panel desktop UI built with DearPyGui

## Why Voice Scribe Exists

The most distinctive part of the project is `Voice Scribe`.

I built it because voice dictation is fast, but standard dictation still makes bilingual work awkward. The plugin closes that gap: I can speak naturally in Russian, keep the intent and speed of speech, and get clear English text ready to paste into work tools. That makes it useful for messages, replies, summaries, and fast drafting without switching mental context.

The recent iteration also makes the workflow feel more physical: `Voice Scribe` now emits device-side MIDI cues for recording, context capture, processing, completion, warnings, and cancellation, and `Cancel` is treated as a hard cancel for the active turn at the application level.

## Modes

| Mode | Purpose |
|------|---------|
| `Productivity` | Everyday editing and clipboard shortcuts |
| `Development` | Coding-oriented shortcuts and editor actions |
| `Media` | Playback, desktop, and quick utility actions |
| `OBS` | Recording, streaming, and scene-related control |
| `Voice Scribe` | Voice-first bilingual writing workflow with chat memory, hard cancel, and MIDI status cues |
| `Sound Pads` | Sample triggering through the sampler plugin |

## Configuration

The main controller behavior lives in `config.toml`.

There you can:

- choose the MIDI device name
- define modes and pad labels
- map pads to keystrokes, shell commands, launchers, OBS actions, and more
- map knobs to volume or plugin behavior
- define app-context rules for automatic mode changes

The same MIDI device name is also used by the feedback layer to find the controller's output port for short notification phrases.

## Related Docs

- [`plugins.md`](plugins.md) for the plugin catalog
- [`../README.md`](../README.md) for the concise landing page
- [`../researches/2026-03-19_voice-feedback-cues.md`](../researches/2026-03-19_voice-feedback-cues.md) for the current MIDI feedback and cancel behavior notes
