# Web UI — MIDI Macropad

Альтернативный фронтенд на React + dockview. DearPyGui приложение (main.py) продолжает работать.

## Быстрый старт

```
Double-click:  MIDI Macropad Web.bat
```

Или вручную:
```bash
cd projects/midi-macropad
python web_main.py --browser     # открывает в браузере
python web_main.py --dev         # dev mode (для разработки с Vite HMR)
python web_main.py               # pywebview desktop shell
```

## Launcher (launcher.pyw)

GUI менеджер с кнопками:
- **Open in Browser** — открыть UI
- **Rebuild UI** — пересобрать фронтенд без перезапуска бэкенда
- **Restart** — перезапустить бэкенд
- **Stop & Exit** — остановить всё

Автоматически:
- Собирает фронтенд если нет `frontend/dist/`
- Перезапускает бэкенд при crash'е (max 3 раза)
- Определяет дубликаты (PID + port check)

## Архитектура

```
┌─ Browser / pywebview ──────────────────────────┐
│  React + dockview + Zustand                    │
│  ↕ WebSocket ws://localhost:8741/ws            │
│  FastAPI + uvicorn (backend/)                  │
│  AppCore: Mapper, MIDI, OBS, Plugins, Audio    │
└────────────────────────────────────────────────┘
```

## Панели

Все панели можно свободно перемещать, группировать в табы, разделять, делать floating.

### Freeform pad / knob панели

Pad/Knob panels — **freeform**: можно открыть сколько угодно через `Controls → Add Pad Panel / Add Knob Panel`. Каждая панель хранит свой `{type, bank, preset, title}` и engineering-кнопку активации.

**Эксклюзивность:** на пару `(type, bank)` активна только одна панель. Нажатие activate на новой панели автоматически деактивирует предыдущую. `pad:A` + `pad:B` могут быть активны одновременно — общий MIDI один, банк определяется по hardware-ноте (16–23 = A, 24–31 = B). Аналогично для knob.

**Неактивная панель** полностью редактируема (label, action, preset, bank), просто не получает MIDI events. Активная подсвечена зелёным LED `● ACTIVE`, неактивная — пустым кругом `○ INACTIVE`.

API: `GET/POST /api/panels`, `PATCH /api/panels/{id}`, `DELETE /api/panels/{id}`, `POST /api/panels/{id}/activate`.

### Каталог панелей

| Панель | Что показывает |
|--------|---------------|
| Pad Panel | Generic freeform pad bank: bank-selector A/B, preset-dropdown, активация, drag & drop swap |
| Knob Panel | Generic freeform knob bank: bank-selector, preset, активация |
| Piano | SFZ/SF2 instrument selector, 2 октавы клавиатуры, FX-параметры (volume / filter / pitch / chorus / delay / reverb / pan) |
| Properties | Action Picker, label, hotkey, lock indicator |
| Log | Real-time лог MIDI/OBS/Plugin/Knob событий |
| OBS | Scene, recording, streaming, replay buffer |
| Voicemeeter | Strips, buses, processing, audience, ducking |
| Voice Scribe | Status, last output, prompt cards, chat history |
| Settings | Profiles, MIDI device, audio engine (sample rate / block / polyphony / device), plugins, OBS connection |

Меню: **☰** в правом верхнем углу. Структура с саб-меню:
- **Controls** → Add Pad Panel / Add Knob Panel
- **Plugins** → Piano / Voice Scribe / OBS / Voicemeeter
- **Settings** → Settings / Properties
- **Logs** → Log
- **Reset Layout**

## Audio engine (Piano)

`plugins/piano/audio_engine.py` — producer/consumer архитектура:

- **Producer thread** владеет голосами и FX chain, читает команды из `queue.Queue` (`note_on/note_off/reconfigure/set_fx_param/stop_all`).
- **Lock-free ring buffer** (`ring_buffer.py`) — SPSC numpy `(N, 2)` float32, индексы атомарны через GIL.
- **Audio callback** копирует готовый блок: `ring.read_into(outdata)` + underrun counter. **Ноль аллокаций** в audio-потоке.
- **Stereo FX chain** работает in-place на `(N, 2)` буфере. Pan реально панорамирует, reverb/chorus/delay держат independent per-channel state.
- **WASAPI shared low-latency** с fallback на дефолтный host API.
- **Pre-computed fade-out window** (~5 ms) применяется через умножение view.
- **Voice stealing** до добавления (приоритет releasing voices).
- **Reconfigure** через queue + `threading.Event` — безопасен от race с MIDI events.

Метрики (по умолчанию sr=44100, blocksize=1024):
- Latency на блок: ~23 ms.
- Voice stealing: 8 голосов default (настраивается в Settings → Audio (Piano)).
- FX chain: `volume → filter → pitch → chorus → delay → reverb → pan`.

## Разработка

```bash
# Терминал 1 — бэкенд с hot reload
python web_main.py --dev

# Терминал 2 — фронтенд с Vite HMR
cd frontend && npm run dev
```

Vite проксирует `/api` и `/ws` на `localhost:8741`.

### Структура

```
backend/
  core.py              — AppCore (headless сервисы)
  app.py               — FastAPI routes + WebSocket
  event_bus.py          — Thread → AsyncIO мост
  operation_manager.py  — Async tasks с идемпотентностью
  middleware.py         — Request logging

frontend/src/
  panels/              — Dockview панели (React компоненты)
  stores/              — Zustand state management
  ws/                  — WebSocket provider
  hooks/               — Layout persistence, tab leader election
  components/          — PresetBar, StatusBar
```

## Headless mode

Web UI запускает плагины в headless mode (`MACROPAD_HEADLESS=1`). Это значит:
- Plugin poll() отключён (DearPyGui и Voicemeeter DLL не thread-safe)
- MIDI pad press/release/knob работают нормально
- UI-обновления плагинов (dpg.*) пропускаются через headless guards
- Voicemeeter данные читаются через getattr() раз в 3 секунды

## Multi-tab

- BroadcastChannel leader election
- Только leader сохраняет layout на бэкенд
- Второй таб показывает read-only banner
- При закрытии leader'а — второй таб становится leader'ом

## TODO / Планы

- [x] Полноценный Action Picker с save на бэкенд (PATCH /api/pads/{note})
- [x] Hotkey capture (keyboard listener на фронте)
- [x] Piano panel + audio engine (Wave D — producer/consumer + stereo FX)
- [x] Freeform pad/knob panels с эксклюзивностью (Wave D)
- [x] Audio settings panel (sample rate / block size / polyphony / device)
- [ ] Voicemeeter live levels через DLL polling в отдельном процессе
- [ ] OBS session panel (start/stop session, segments, diary)
- [ ] REAPER Bridge панель
- [ ] Sample Player панель с waveform
- [ ] Тёмная/светлая тема
- [ ] WebSocket bidirectional (client → server commands)
- [ ] pywebview native window menu
- [ ] Installer / portable build
- [ ] FX-цепь: убрать producer-thread аллокации в Pan/Pitch/Filter (GC-джиттер на малых блоках)
