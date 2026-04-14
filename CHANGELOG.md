# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] - 2026-04-13 — Freeform panels, Piano audio engine

### Added — Wave C/D

- **Piano plugin** (`plugins/piano/`): SFZ/SF2 polyphonic synth with 7-effect stereo FX chain (volume / filter / pitch / chorus / delay / reverb / pan). 3 built-in instruments (sine-piano / electric-piano / organ).
- **Lock-free audio engine** (`plugins/piano/audio_engine.py`, `ring_buffer.py`):
  - Producer/consumer architecture — audio callback is **zero-allocation** (just `ring.read_into(outdata)` + underrun counter).
  - SPSC numpy ring buffer, GIL-atomic indices.
  - WASAPI shared low-latency with fallback to default host API.
  - Pre-computed fade windows, voice stealing before append, reconfigure via command queue (race-free).
  - Stereo FX chain `(N, 2)` in-place — Pan now actually pans, reverb/chorus/delay keep independent per-channel state.
- **Freeform Pad/Knob panels**: Pad and Knob panels are no longer fixed slots. Open as many as you like, each with its own bank+preset+title+activate state.
  - Exclusivity: only one panel can be active per `(type, bank)`. Activating another auto-deactivates the previous. `pad:A` + `pad:B` can both be active simultaneously.
  - Engineering-style activate button: monospace LED indicator (`● ACTIVE` solid green / `○ INACTIVE` outline).
  - Inactive panels remain fully editable; they just don't receive MIDI events.
  - REST API: `GET/POST/PATCH/DELETE /api/panels`, `POST /api/panels/{id}/activate`.
- **Hierarchical submenu** in the toolbar: Controls (Add Pad / Add Knob), Plugins, Settings, Logs. Submenus fly out to the left; menu auto-closes with 150ms debounce so you can navigate diagonally.
- **Audio settings panel** in Settings: sample rate, block size, max polyphony, latency mode, output device, master volume — applied via `piano_plugin.reconfigure()` without restart.
- **Composite preset keys** (`preset:note`) in `pad_registry`/`mapper`/API/frontend (Wave C foundation for freeform model).

### Changed

- **Backend dispatch**: `mapper.lookup_pad_for_active(note)` / `lookup_knob_for_active_bank(cc)` resolve through the active panel's preset rather than a global preset.
- **Migration**: legacy `panel_presets.padBank-A/B` and `knobBank-A/B` keys are converted on first boot to 4 starter freeform panels (pad A active, pad B inactive, knob A active, knob B inactive). `ui_layout` is reset to let the new default layout build cleanly.
- **WS events**: `panel.created`, `panel.updated`, `panel.deleted`, `panel.activated` replace the old per-panel-preset events.

### Removed

- Hard-coded `padBank-A/B` / `knobBank-A/B` panel IDs and the corresponding K A / K B chip indicator.
- Module-level `MAX_VOICES` global in piano plugin (now `self._max_voices` in engine).

### Tests

- 103/103 pytest passing (88 → 103, +7 ring_buffer, +8 audio_engine, +14 panel-model regression tests).

---

## [2.1.0] - 2026-04-09 — Legacy cleanup

### Removed — старый Python UI (DearPyGui)

Web UI стал единственным фронтендом. Полностью удалён старый desktop UI.

- `main.py` (1103 строки) — старая точка входа DearPyGui
- `ui/` — все DearPyGui компоненты (dashboard, pad_grid, pad_editor, quick_action_picker, sidebar_left/right, toolbar, status_bar, volume_panel, midi_log, selection)
- `app_detector.py` — foreground window detection (использовалось только `main.py`)
- `MIDI Macropad.bat`, `MIDI Macropad Desktop Shortcut.lnk` — старые ярлыки запуска
- `docs/ui-mockup-*.html`, `docs/voicemeeter-integration.html` — мокапы прототипа
- `__pycache__/`, `backups/` — артефакты и старые бэкапы

### Changed

- **`README.md`** полностью переписан под Web UI (убрано упоминание `main.py`, добавлен launcher quickstart).
- **`.gitignore`**: `obs_backup/` и `researches/` добавлены (локально хранятся, но не в репозитории).
- **`mapper.py`**: убрано упоминание legacy `[[modes]]` формата в docstring (парсера уже не было).
- **`plugins/voice_scribe/scribe.py`**: убран lazy-import `ui.pad_grid` в `_reload_and_refresh_pads` — Web UI обновляет labels через WebSocket.
- **`plugins/obs_session/obs_session.py`**: убран backward-compat код миграции `working_scene` (все settings.json уже смигрированы).
- **`plugins/voicemeeter/voicemeeter.py`**: убран устаревший комментарий со ссылкой на `main.py`.
- **`backend/core.py`**: обновлён заголовок секции Action execution (убрана ссылка на `main.py`).

### Migration notes

Если вы запускали приложение через `python main.py` или `MIDI Macropad.bat` — теперь используйте `python launcher.pyw` или `MIDI Macropad Web.bat`.

---

## [2.0.0] - 2026-04-02 — Web UI

### Added — Web UI (альтернативный фронтенд)

**Архитектура:**
- React + TypeScript + Vite фронтенд с dockview (свободный докинг панелей)
- FastAPI + uvicorn бэкенд с WebSocket real-time events
- pywebview интеграция для desktop shell (опционально)
- Zustand state management с WebSocket sync
- `web_main.py` — entry point (--dev, --browser, или pywebview)
- `launcher.pyw` — tkinter GUI менеджер процессов (double-click запуск)
- `MIDI Macropad Web.bat` — ярлык для Explorer

**Панели (все dockable — drag/tab/split/float):**
- **Pad Grid** — 2 банка × 4 пада + 4 knob'а, drag & drop swap, flash при MIDI
- **Properties** — Action Picker с grouped chips (System/OBS/VM), hotkey, label
- **Log** — real-time лог с auto-scroll и цветными prefix'ами
- **OBS** — статус подключения, сцена, запись, replay buffer
- **Voicemeeter** — strips (MIC/DESKTOP/SEND2MIC), buses, processing, audience, ducking
- **Voice Scribe** — phase indicator, last output (original + result), prompt cards, chat
- **Settings** — profiles, MIDI device, general (transpose, caps), plugins toggle, OBS connection

**Инфраструктура:**
- PresetBar с chips + меню ☰ (открыть любую панель) + ⚙ (Settings)
- StatusBar с MIDI/OBS/Scene индикаторами
- Toast уведомления с анимацией
- Layout persistence на бэкенд (settings.json) с localStorage fallback
- Multi-tab protection (BroadcastChannel leader election)
- OperationManager — async background tasks с идемпотентностью и progress
- RequestLoggingMiddleware — HTTP запросы с таймингом
- Global exception handler → WebSocket push → toast
- RotatingFileHandler (5MB × 3)
- Auto-restart бэкенда при crash (max 3 попытки, backoff)
- Rebuild UI кнопка в лаунчере (без рестарта бэкенда)

**Headless mode (MACROPAD_HEADLESS=1):**
- Plugins работают без DearPyGui — headless guards в _refresh_ui, _rebuild_pad_grid, _set_status
- Plugin poll отключён (DLL thread-safety), MIDI events работают
- Thread safety: RLock на shared state (preset switch, pad swap, MIDI events)

**REST API:**
- GET /api/state, /api/pads, /api/presets, /api/plugins, /api/midi/status
- POST /api/pads/{note}/press, /api/pads/swap, /api/presets/{index}/activate
- GET/PUT /api/settings/{key}, GET /api/profiles, POST /api/profiles/{name}/load|save
- POST /api/plugins/{name}/toggle, /api/midi/reconnect
- GET /api/voice-scribe/state, POST /api/voice-scribe/new-chat
- POST /api/ops/start, GET /api/ops/{id}, POST /api/ops/{id}/cancel

**Совместимость:**
- main.py (DearPyGui) НЕ затронут — оба фронтенда работают независимо
- Общие модули: mapper, midi_listener, audio, settings, plugins — без изменений
- 3 файла плагинов изменены (только headless guards, обратно совместимо)

## [Unreleased] - 2026-03-29

### Added

- Pad editor: plugin action type includes a **Plugin Target** field; saved pad config stores the target in the action path
- Plugin base API: `set_owned_notes`, `get_action_catalog`, `execute_plugin_action`, and `get_dynamic_label` for owned-note ranges, discoverable actions, and live pad labels
- Spotify plugin with OAuth PKCE integration for Web API control
  - Play/Pause, Next, Previous, Like, Shuffle via Spotify API
  - DJ Mix pad (keyboard shortcut), Add to Playlist, Remove from Playlist (API)
  - Right sidebar: OAuth connection, now-playing info, pad mapping docs
  - Center tab: large now-playing view with transport controls
  - Background polling for track state every 3 seconds
  - Automatic token refresh
- Spotify app volume control via Knob 3 (CC50) using Windows audio sessions
- Plugin selector combo in right sidebar to open any plugin's properties
- Mode icons and redesigned mode selection buttons with custom themes
- Unique MIDI mini-melodies (~2s) per mode for auditory feedback on switch
- Joystick Y-axis (CC16) dedicated to mode switching (up/down)
- Joystick X-axis (pitch bend) for real-time melody transposition (±24 semitones)
- Transpose value persists across sessions (`melody_transpose` setting)
- All mode melodies normalized to C major for consistent tonality
- MIDI cues `session.start`, `session.stop`, `session.segment_start`, and `session.segment_stop` (steel guitar, low-mid) in `feedback.py`

### Fixed

- Plugin activation: `set_owned_notes()` now sets `self._active = bool(notes)` in Spotify, Voicemeeter, OBS Session, Voice Scribe, Sample Player, and Performance template plugins, fixing presets with arbitrary names where `on_mode_changed` left every plugin inactive

### Changed

- Left sidebar: **PAD PRESET** dropdown replaces mode controls; device section shows the MIDI port; mode buttons and the current-mode line removed
- PluginManager calls `notify_preset_changed(mapper)` on preset change so plugins receive updated owned note ranges
- OBS, Spotify, Voicemeeter, Voice Scribe, Sample Player, and Performance Template plugins use dynamic pad-to-note mapping via `set_owned_notes` where applicable
- Default window size doubled to 2400×1560 for high-resolution displays
- Voicemeeter joystick binding removed; joystick is now global mode switcher
- Mode order: Spotify, Voicemeeter, Voice Scribe placed first in the list

### Removed

- Productivity, Development, Media mode sections (broken/unused)
- Spotify Liked Songs, Search, Repeat pads (replaced with DJ Mix, playlist actions)

### Requirements

- Spotify Premium account
- Client ID from developer.spotify.com
