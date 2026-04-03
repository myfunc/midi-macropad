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

| Панель | Что показывает |
|--------|---------------|
| Pad Grid | 2 банка × 4 пада + knobs, drag & drop swap |
| Properties | Action Picker, label, hotkey, lock indicator |
| Log | Real-time лог MIDI/OBS/Plugin событий |
| OBS | Scene, recording, streaming, replay buffer |
| Voicemeeter | Strips, buses, processing, audience, ducking |
| Voice Scribe | Status, last output, prompt cards, chat history |
| Settings | Profiles, MIDI device, general, plugins, OBS connection |

Доступ: **☰** меню в preset bar или **⚙** для Settings.

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

- [ ] Полноценный Action Picker с save на бэкенд (PATCH /api/pads/{note})
- [ ] Voicemeeter live levels через DLL polling в отдельном процессе
- [ ] OBS session panel (start/stop session, segments, diary)
- [ ] Drag & drop между банками (cross-bank swap)
- [ ] REAPER Bridge панель
- [ ] Sample Player панель с waveform
- [ ] Hotkey capture (keyboard listener на фронте)
- [ ] Тёмная/светлая тема
- [ ] WebSocket bidirectional (client → server commands)
- [ ] pywebview native window menu
- [ ] Installer / portable build
