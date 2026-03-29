# Project Context

## What this project is
MIDI Macropad turns an Akai MPK Mini Play (or similar MIDI gear) into a Windows control surface: app-aware modes, shortcuts, audio, OBS, Spotify, Voice Scribe, and plugins.

## Current state
Core app and plugin host run; modes are driven by `config.toml`. **OBS** is a single unified mode (March 2026): three scenes via `setup_three_scenes()`, pad map for scenes + mic + session/record + two spare pads, settings for `scene_screen` / `scene_camera` / `scene_pip`, and new session-related MIDI feedback cues. Legacy dual “OBS” + “OBS Session” configuration is removed. Latest shipped change: commit `8712376` on `master`.

## Tech stack
Python, DearPyGui, mido; OBS via WebSocket; optional Spotify Web API; plugins under `plugins/`.

## Key patterns
- Modes and routing in `config.toml` + `main.py`; per-`mode` cue maps in `feedback.py`.
- OBS logic split between `obs_controller.py` and `plugins/obs_session/` (OBS plugin implementation).
- Device-side MIDI feedback phrases for state and errors.

## Known issues
- README mode list may still mention “OBS Session” separately; align docs when editing README next.
- Review score 78/100 suggests further polish possible (non-blocking).
