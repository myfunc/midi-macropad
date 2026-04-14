"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import logging
import traceback
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from backend.core import AppCore
from backend.middleware import RequestLoggingMiddleware

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
log = logging.getLogger("midi_macropad.app")


def create_app(core: AppCore) -> FastAPI:
    app = FastAPI(title="MIDI Macropad", version="1.0.0")
    app.state.core = core

    # Middleware
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://127.0.0.1:5173",
            "http://10.0.0.27:5173", "http://10.0.0.27:8741",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Global error handler ----------------------------------------

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        tb = traceback.format_exc()
        log.error("Unhandled %s %s: %s\n%s", request.method, request.url.path, exc, tb)
        core.event_bus.publish("error.unhandled", {
            "path": str(request.url.path),
            "error": str(exc),
        })
        core._runtime_log("ERR", f"HTTP {request.url.path}: {exc}", (255, 80, 80))
        return JSONResponse({"error": "Internal server error", "detail": str(exc)}, 500)

    # -- REST routes --------------------------------------------------

    @app.get("/api/state")
    async def get_state():
        return JSONResponse(core.get_state_snapshot())

    @app.get("/api/midi/status")
    async def midi_status():
        return {
            "connected": core.midi.connected,
            "port_name": core.midi.port_name,
            "device_name": core.config.device_name,
        }

    @app.post("/api/midi/reconnect")
    async def midi_reconnect():
        core.midi.reconnect()
        core.event_bus.publish("midi.status", {
            "connected": core.midi.connected,
            "port_name": core.midi.port_name,
        })
        return {"ok": True, "connected": core.midi.connected}

    @app.get("/api/pads")
    async def get_pads():
        return core.get_state_snapshot()["pads"]

    @app.post("/api/pads/{note}/press")
    async def pad_press(note: int):
        from midi_listener import MidiEvent
        import time
        event = MidiEvent("pad_press", time.time(), note=note, velocity=100)
        core.event_queue.put(event)
        return {"ok": True}

    @app.post("/api/pads/swap")
    async def pad_swap(body: dict):
        preset = body.get("preset")
        note_a = body.get("note_a")
        note_b = body.get("note_b")
        if note_a is None or note_b is None:
            return JSONResponse({"error": "note_a and note_b required"}, 400)
        if not preset:
            return JSONResponse({"error": "preset name required"}, 400)
        pad_a = core.mapper.registry.get_pad(preset, note_a)
        pad_b = core.mapper.registry.get_pad(preset, note_b)
        if (pad_a and pad_a.locked) or (pad_b and pad_b.locked):
            return JSONResponse({"error": "Cannot swap locked pads"}, 409)
        with core._lock:
            core.mapper.swap_pads(preset, note_a, note_b)
        from mapper import save_config
        from backend.core import CONFIG_PATH
        save_config(core.config, CONFIG_PATH)
        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    @app.post("/api/knobs/swap")
    async def knob_swap(body: dict):
        cc_a = body.get("cc_a")
        cc_b = body.get("cc_b")
        if cc_a is None or cc_b is None:
            return JSONResponse({"error": "cc_a and cc_b required"}, 400)
        ok = core.swap_knobs(cc_a, cc_b)
        if not ok:
            return JSONResponse({"error": "One or both CCs not found"}, 404)
        return {"ok": True}

    # -- Preset-aware pad update --------------------------------------

    @app.patch("/api/presets/{preset_name}/pads/{note}")
    async def update_preset_pad(preset_name: str, note: int, body: dict):
        """Update pad label, hotkey, and/or action in a specific preset."""
        pad = core.mapper.registry.get_pad(preset_name, note)
        # Build defaults from existing pad or empty
        old_label = pad.label if pad else ""
        old_hotkey = pad.hotkey if pad else ""
        old_action_type = pad.action_type if pad else ""
        old_action_data = pad.action_data if pad else {}

        label = body.get("label", old_label)
        hotkey = body.get("hotkey", old_hotkey)
        action_data = body.get("action")
        if action_data is None:
            action_dict = {"type": old_action_type}
            action_dict.update(old_action_data)
        else:
            action_dict = action_data

        log.info("PATCH preset=%s pad %d: label=%r hotkey=%r action=%r",
                 preset_name, note, label, hotkey, action_dict)
        with core._lock:
            core.mapper.update_pad(preset_name, note, label, action_dict, hotkey=hotkey)
            core.hotkeys.reload_bindings(core.mapper)
        # Save config to disk
        from mapper import save_config
        from backend.core import CONFIG_PATH
        save_config(core.config, CONFIG_PATH)

        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    @app.delete("/api/presets/{preset_name}/pads/{note}/action")
    async def clear_preset_pad_action(preset_name: str, note: int):
        """Clear pad action and hotkey in a specific preset."""
        with core._lock:
            core.mapper.update_pad(preset_name, note, "", {"type": ""}, hotkey="")
            core.hotkeys.reload_bindings(core.mapper)
        from mapper import save_config
        from backend.core import CONFIG_PATH
        save_config(core.config, CONFIG_PATH)
        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    # -- Legacy pad routes (kept for backward compat) -----------------

    @app.patch("/api/pads/{note}")
    async def update_pad(note: int, body: dict):
        """Update pad in current preset (legacy route)."""
        preset_name = core.mapper.current_preset.name
        pad = core.mapper.registry.get_pad(preset_name, note)
        old_label = pad.label if pad else ""
        old_hotkey = pad.hotkey if pad else ""
        old_action_type = pad.action_type if pad else ""
        old_action_data = pad.action_data if pad else {}

        label = body.get("label", old_label)
        hotkey = body.get("hotkey", old_hotkey)
        action_data = body.get("action")
        if action_data is None:
            action_dict = {"type": old_action_type}
            action_dict.update(old_action_data)
        else:
            action_dict = action_data
        log.info("PATCH pad %d (legacy, preset=%s): label=%r hotkey=%r action=%r",
                 note, preset_name, label, hotkey, action_dict)
        with core._lock:
            core.mapper.update_pad(preset_name, note, label, action_dict, hotkey=hotkey)
            core.hotkeys.reload_bindings(core.mapper)
        from mapper import save_config
        from backend.core import CONFIG_PATH
        save_config(core.config, CONFIG_PATH)

        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    @app.delete("/api/pads/{note}/action")
    async def clear_pad_action(note: int):
        """Clear pad action and hotkey (legacy route, uses current preset)."""
        preset_name = core.mapper.current_preset.name
        with core._lock:
            core.mapper.update_pad(preset_name, note, "", {"type": ""}, hotkey="")
            core.hotkeys.reload_bindings(core.mapper)
        from mapper import save_config
        from backend.core import CONFIG_PATH
        save_config(core.config, CONFIG_PATH)
        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    @app.get("/api/presets")
    async def get_presets():
        return {
            "current_index": core.mapper.current_preset_index,
            "list": [
                {"index": i, "name": p.name}
                for i, p in enumerate(core.config.pad_presets)
            ],
        }

    @app.post("/api/presets/{index}/activate")
    async def activate_preset(index: int):
        if not (0 <= index < len(core.config.pad_presets)):
            return JSONResponse({"error": "invalid index"}, 400)
        try:
            with core._lock:
                core.mapper.set_preset(index)
            import settings
            settings.put("preset_index", index)
            try:
                core.plugin_manager.on_mode_changed(core.mapper.current_preset.name)
            except Exception as exc:
                log.warning("on_mode_changed error: %s", exc)
            try:
                core.plugin_manager.notify_preset_changed(core.mapper)
            except Exception as exc:
                log.warning("notify_preset_changed error: %s", exc)
            # Play mode melody via MIDI feedback
            mode_key = core.mapper.current_preset.name.lower().replace(" ", "_")
            cue_id = f"mode.{mode_key}"
            core.feedback.emit(cue_id)

            core.event_bus.publish("preset.changed", {
                "index": index,
                "name": core.mapper.current_preset.name,
                "pads": core.get_state_snapshot()["pads"],
            })
            return {"ok": True, "name": core.mapper.current_preset.name}
        except Exception as exc:
            log.error("activate_preset failed: %s", exc, exc_info=True)
            return JSONResponse({"error": str(exc)}, 500)

    # -- Freeform panels ----------------------------------------------

    @app.get("/api/panels")
    async def list_panels():
        return {
            "panels": core.list_panels(),
            "active_panels": core.mapper.get_all_active_panels(),
        }

    @app.post("/api/panels")
    async def create_panel(body: dict):
        panel_type = body.get("type")
        bank = body.get("bank", "A")
        preset = body.get("preset")
        title = body.get("title")
        activate = bool(body.get("activate", False))
        if panel_type not in ("pad", "knob"):
            return JSONResponse({"error": "type must be 'pad' or 'knob'"}, 400)
        if bank not in ("A", "B"):
            return JSONResponse({"error": "bank must be 'A' or 'B'"}, 400)
        try:
            panel = core.create_panel(panel_type, bank=bank,
                preset=preset, title=title, activate=activate)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        return panel

    @app.patch("/api/panels/{instance_id}")
    async def patch_panel(instance_id: str, body: dict):
        panel = core.update_panel(
            instance_id,
            bank=body.get("bank"),
            preset=body.get("preset"),
            title=body.get("title"),
        )
        if panel is None:
            return JSONResponse({"error": f"panel '{instance_id}' not found"}, 404)
        return panel

    @app.delete("/api/panels/{instance_id}")
    async def remove_panel(instance_id: str):
        if not core.delete_panel(instance_id):
            return JSONResponse({"error": f"panel '{instance_id}' not found"}, 404)
        return {"ok": True}

    @app.post("/api/panels/{instance_id}/activate")
    async def activate_panel(instance_id: str):
        if not core.activate_panel(instance_id):
            return JSONResponse({"error": f"panel '{instance_id}' not found"}, 404)
        return {"ok": True}

    @app.post("/api/panels/reconcile")
    async def reconcile_panels(body: dict | None = None):
        fix_titles = bool((body or {}).get("fix_titles", False))
        try:
            result = core.reconcile_panels(fix_titles=fix_titles)
        except Exception as exc:
            log.error("reconcile_panels failed: %s", exc, exc_info=True)
            return JSONResponse({"error": str(exc)}, 500)
        return result

    # -- Panel presets (legacy, kept for compatibility) ---------------

    @app.get("/api/panel-presets/{panel_id}")
    async def get_panel_preset(panel_id: str):
        info = core.get_panel_preset(panel_id)
        if info is None:
            return JSONResponse({"error": f"Panel '{panel_id}' not found"}, 404)
        return info

    @app.put("/api/panel-presets/{panel_id}/order")
    async def set_panel_order(panel_id: str, body: dict):
        order = body.get("order")
        if not isinstance(order, list):
            return JSONResponse({"error": "order must be a list"}, 400)
        ok = core.set_panel_order(panel_id, order)
        if not ok:
            return JSONResponse({"error": f"Invalid panel '{panel_id}'"}, 400)
        return {"ok": True}

    @app.post("/api/presets")
    async def create_preset(body: dict):
        name = body.get("name")
        if not name:
            return JSONResponse({"error": "name required"}, 400)
        ok, err = core.create_preset(name)
        if not ok:
            return JSONResponse({"error": err}, 409)
        core.event_bus.publish("presets.changed", {
            "presets": [
                {"index": i, "name": p.name}
                for i, p in enumerate(core.config.pad_presets)
            ],
        })
        return {"ok": True, "name": name}

    @app.delete("/api/presets/{name}")
    async def delete_preset(name: str):
        ok, err = core.delete_preset(name)
        if not ok:
            status = 404 if "not found" in err.lower() else 409
            return JSONResponse({"error": err}, status)
        core.event_bus.publish("presets.changed", {
            "presets": [
                {"index": i, "name": p.name}
                for i, p in enumerate(core.config.pad_presets)
            ],
        })
        return {"ok": True}

    @app.patch("/api/presets/{name}")
    async def rename_preset(name: str, body: dict):
        new_name = body.get("name")
        if not new_name:
            return JSONResponse({"error": "new name required"}, 400)
        ok, err = core.rename_preset(name, new_name)
        if not ok:
            status = 409 if "already exists" in err.lower() else 404
            return JSONResponse({"error": err}, status)
        core.event_bus.publish("presets.changed", {
            "presets": [
                {"index": i, "name": p.name}
                for i, p in enumerate(core.config.pad_presets)
            ],
        })
        return {"ok": True, "old_name": name, "new_name": new_name}

    @app.get("/api/plugins")
    async def get_plugins():
        result = []
        for info in core.plugin_manager.discover():
            result.append({
                "name": info["name"],
                "version": info.get("version", ""),
                "description": info.get("description", ""),
                "enabled": info["name"] in core.plugin_manager.enabled,
            })
        return result

    @app.get("/api/telemetry/current")
    async def get_telemetry_current():
        """Live in-memory session telemetry snapshot (no disk write)."""
        return core.telemetry.snapshot()

    @app.post("/api/telemetry/dump")
    async def post_telemetry_dump():
        """Force an immediate telemetry dump to disk and return the file path."""
        try:
            path = core.telemetry.dump()
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.get("/api/knob-presets")
    async def get_knob_presets():
        return core.get_knob_presets()

    @app.get("/api/knobs/catalog")
    async def get_knob_catalog():
        """Aggregated knob action catalog from all enabled plugins."""
        core_actions = [
            {"id": "volume:master", "type": "volume", "target": "master",
             "label": "Master Volume", "description": "System master volume",
             "params_schema": {}},
            {"id": "volume:mic", "type": "volume", "target": "mic",
             "label": "Mic Volume", "description": "System mic input volume",
             "params_schema": {}},
            {"id": "volume:spotify", "type": "volume", "target": "spotify",
             "label": "Spotify Volume", "description": "Spotify app volume",
             "params_schema": {}},
            {"id": "volume:foreground", "type": "volume", "target": "foreground",
             "label": "Foreground App Volume",
             "description": "Currently focused app volume",
             "params_schema": {}},
        ]
        raw_plugins = core.plugin_manager.get_all_knob_catalogs()
        plugins: dict[str, list] = {}
        for plugin_name, actions in raw_plugins.items():
            enriched = []
            for a in actions:
                enriched.append({
                    **a,
                    "type": "plugin",
                    "target": f"{plugin_name}:{a['id']}",
                })
            plugins[plugin_name] = enriched
        return {
            "core": core_actions,
            "plugins": plugins,
        }

    @app.put("/api/knobs/{cc}/action")
    async def update_knob_action(cc: int, body: dict):
        """Update knob action in config.toml."""
        action_type = body.get("type")
        target = body.get("target")
        label = body.get("label", f"CC{cc}")
        params = body.get("params", {})
        if not action_type or not target:
            return JSONResponse({"error": "type and target required"}, 400)
        ok = core.update_knob_config(cc, action_type, target, label, params)
        if not ok:
            return JSONResponse({"error": f"Knob CC{cc} not found in config"}, 404)
        return {"ok": True, "cc": cc, "label": label}

    # -- Piano plugin --------------------------------------------------

    @app.get("/api/piano/instruments")
    async def get_piano_instruments():
        piano = core.plugin_manager.plugins.get("Piano")
        if not piano:
            return JSONResponse({"error": "Piano plugin not loaded"}, 404)
        return {
            "instruments": piano.available_instruments(),
            "current": piano.current_instrument,
        }

    @app.post("/api/piano/instrument")
    async def set_piano_instrument(body: dict):
        piano = core.plugin_manager.plugins.get("Piano")
        if not piano:
            return JSONResponse({"error": "Piano plugin not loaded"}, 404)
        name = body.get("name")
        if not name:
            return JSONResponse({"error": "name required"}, 400)
        ok = piano.load_instrument(name)
        core.event_bus.publish("piano.instrument_changed", {
            "name": name,
            "success": ok,
            "current": piano.current_instrument,
        })
        return {"ok": ok, "current": piano.current_instrument}

    @app.post("/api/piano/note")
    async def piano_note_on(body: dict):
        note = body.get("note")
        velocity = body.get("velocity", 100)
        bank = body.get("bank")  # optional: 'play' | 'map'
        if note is None:
            return JSONResponse({"error": "note required"}, 400)
        if not (0 <= note <= 127):
            return JSONResponse({"error": "note must be 0-127"}, 400)
        if not (0 <= velocity <= 127):
            return JSONResponse({"error": "velocity must be 0-127"}, 400)

        # If bank='map' is specified, route through the piano dispatcher
        # which respects the active map panel + preset mapping.
        if bank == "map":
            handled = core.handle_piano_note(int(note), int(velocity), on=True)
            return {"ok": True, "handled": handled, "bank": "map"}

        piano = core.plugin_manager.plugins.get("Piano")
        if not piano:
            return JSONResponse({"error": "Piano plugin not loaded"}, 404)
        piano._note_on(note, velocity)
        core.event_bus.publish("piano.note_on", {
            "note": note,
            "velocity": velocity,
        })
        return {"ok": True}

    @app.post("/api/piano/note/off")
    async def piano_note_off(body: dict):
        note = body.get("note")
        bank = body.get("bank")
        if note is None:
            return JSONResponse({"error": "note required"}, 400)
        if bank == "map":
            handled = core.handle_piano_note(int(note), 0, on=False)
            return {"ok": True, "handled": handled, "bank": "map"}
        piano = core.plugin_manager.plugins.get("Piano")
        if not piano:
            return JSONResponse({"error": "Piano plugin not loaded"}, 404)
        piano._note_off(note)
        core.event_bus.publish("piano.note_off", {"note": note})
        return {"ok": True}

    # -- Piano presets (for Piano Map bank) ---------------------------

    def _serialize_piano_preset(pp) -> dict:
        return {
            "name": pp.name,
            "keys": [
                {
                    "note": k.note,
                    "label": k.label,
                    "action": k.action,
                }
                for k in pp.keys
            ],
        }

    @app.get("/api/piano/presets")
    async def list_piano_presets():
        return {
            "presets": [
                _serialize_piano_preset(pp)
                for pp in core.config.piano_presets
            ],
        }

    @app.get("/api/piano/presets/{name}")
    async def get_piano_preset(name: str):
        pp = core.mapper.get_piano_preset(name)
        if pp is None:
            return JSONResponse({"error": f"preset '{name}' not found"}, 404)
        return _serialize_piano_preset(pp)

    @app.post("/api/piano/presets")
    async def create_piano_preset(body: dict):
        from mapper import PianoPreset
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, 400)
        if core.mapper.get_piano_preset(name) is not None:
            return JSONResponse({"error": f"preset '{name}' already exists"}, 409)
        from mapper import save_config
        from backend.core import CONFIG_PATH
        with core._lock:
            core.config.piano_presets.append(PianoPreset(name=name))
            save_config(core.config, CONFIG_PATH)
        core.event_bus.publish("piano_presets.changed", {
            "presets": [_serialize_piano_preset(p) for p in core.config.piano_presets],
        })
        return {"ok": True, "name": name}

    @app.delete("/api/piano/presets/{name}")
    async def delete_piano_preset(name: str):
        pp = core.mapper.get_piano_preset(name)
        if pp is None:
            return JSONResponse({"error": f"preset '{name}' not found"}, 404)
        # Refuse if an active piano panel references it.
        panels = core.list_panels()
        for panel in panels.values():
            if (panel.get("type") == "piano"
                    and (panel.get("preset") or "").lower() == name.lower()):
                return JSONResponse(
                    {"error": f"preset '{name}' is in use by a panel"}, 409)
        from mapper import save_config
        from backend.core import CONFIG_PATH
        with core._lock:
            core.config.piano_presets = [
                p for p in core.config.piano_presets
                if p.name.lower() != name.lower()
            ]
            save_config(core.config, CONFIG_PATH)
        core.event_bus.publish("piano_presets.changed", {
            "presets": [_serialize_piano_preset(p) for p in core.config.piano_presets],
        })
        return {"ok": True}

    @app.patch("/api/piano/presets/{name}/keys/{note}")
    async def patch_piano_key(name: str, note: int, body: dict):
        from mapper import PianoKeyMapping, PIANO_MAP_NOTE_MIN, PIANO_MAP_NOTE_MAX
        pp = core.mapper.get_piano_preset(name)
        if pp is None:
            return JSONResponse({"error": f"preset '{name}' not found"}, 404)
        if not (PIANO_MAP_NOTE_MIN <= note <= PIANO_MAP_NOTE_MAX):
            return JSONResponse(
                {"error": f"note {note} out of range "
                 f"[{PIANO_MAP_NOTE_MIN}-{PIANO_MAP_NOTE_MAX}]"}, 400)

        has_label = "label" in body
        has_action = "action" in body
        label = body.get("label")
        action = body.get("action")
        if has_label and label is not None and not isinstance(label, str):
            return JSONResponse(
                {"error": "label must be string or null"}, 400)
        if action is not None and not isinstance(action, dict):
            return JSONResponse(
                {"error": "action must be a dict or null"}, 400)

        existing = None
        for k in pp.keys:
            if k.note == note:
                existing = k
                break
        # No-op guard: if the key doesn't exist and the body carries neither
        # label nor action, don't create an empty entry.
        if existing is None and not has_label and not has_action:
            return {"preset": _serialize_piano_preset(pp)}

        from mapper import save_config
        from backend.core import CONFIG_PATH
        with core._lock:
            # Re-resolve under lock (another writer may have added it).
            existing = None
            for k in pp.keys:
                if k.note == note:
                    existing = k
                    break
            if existing is None:
                existing = PianoKeyMapping(note=note)
                pp.keys.append(existing)
            if has_label:
                existing.label = label if isinstance(label, str) else None
            if has_action:
                existing.action = dict(action) if isinstance(action, dict) else None
            # Garbage-collect entries that end up fully empty (no label, no action).
            if not existing.label and not existing.action:
                pp.keys = [k for k in pp.keys if k is not existing]
            save_config(core.config, CONFIG_PATH)

        core.event_bus.publish("piano_presets.changed", {
            "presets": [_serialize_piano_preset(p) for p in core.config.piano_presets],
        })
        return _serialize_piano_preset(pp)

    @app.get("/api/audio/devices")
    async def get_audio_devices():
        """List available audio output devices via sounddevice."""
        try:
            import sounddevice as sd
            raw_devices = sd.query_devices()
            default_out = None
            try:
                default = sd.default.device
                if isinstance(default, (list, tuple)) and len(default) >= 2:
                    default_out = default[1]
                elif isinstance(default, int):
                    default_out = default
            except Exception:
                pass
            devices = []
            for idx, d in enumerate(raw_devices):
                if int(d.get("max_output_channels", 0)) > 0:
                    devices.append({
                        "index": idx,
                        "name": d.get("name", f"Device {idx}"),
                        "max_output_channels": int(d.get("max_output_channels", 0)),
                        "default": (idx == default_out),
                    })
            # Current device from piano_audio settings (or default)
            import settings as _settings
            piano_audio = _settings.get("piano_audio", {}) or {}
            current = piano_audio.get("output_device", default_out)
            return {"devices": devices, "current": current}
        except Exception as exc:
            log.error("get_audio_devices failed: %s", exc, exc_info=True)
            return JSONResponse({"error": str(exc)}, 500)

    @app.put("/api/settings/piano_audio")
    async def put_piano_audio(body: dict):
        """Save piano audio config and apply to Piano plugin."""
        import settings as _settings
        # Validate & coerce
        try:
            cfg = {
                "sample_rate": int(body.get("sample_rate", 44100)),
                "block_size": int(body.get("block_size", 1024)),
                "max_polyphony": int(body.get("max_polyphony", 8)),
                "latency_mode": str(body.get("latency_mode", "low")),
                "output_device": body.get("output_device"),
                "master_volume": float(body.get("master_volume", 0.8)),
            }
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": f"Invalid config: {exc}"}, 400)

        if cfg["latency_mode"] not in ("low", "medium", "high"):
            return JSONResponse({"error": "latency_mode must be low|medium|high"}, 400)

        _settings.put("piano_audio", cfg)

        piano = core.plugin_manager.plugins.get("Piano")
        if piano is None:
            core.event_bus.publish("settings.changed", {"key": "piano_audio", "value": cfg})
            return {"ok": True, "applied": False, "reason": "Piano plugin not loaded"}
        try:
            ok, err = piano.reconfigure(cfg)
            core.event_bus.publish("settings.changed", {"key": "piano_audio", "value": cfg})
            if not ok:
                return JSONResponse({"ok": False, "applied": False, "error": err or "reconfigure failed"}, 500)
            return {"ok": True, "applied": True}
        except Exception as exc:
            log.error("piano reconfigure failed: %s", exc, exc_info=True)
            return JSONResponse({"ok": False, "applied": False, "error": str(exc)}, 500)

    @app.get("/api/piano/fx")
    async def get_piano_fx():
        piano = core.plugin_manager.plugins.get("Piano")
        if not piano:
            return JSONResponse({"error": "Piano plugin not loaded"}, 404)
        return piano.fx_chain.get_state()

    # -- Settings & Profiles ------------------------------------------

    @app.get("/api/settings")
    async def get_settings():
        import settings
        return {
            "values": settings.get_all(),
            "profiles": settings.list_profiles(),
            "active_profile": settings.active_profile(),
        }

    @app.put("/api/settings/{key}")
    async def put_setting(key: str, body: dict):
        import settings
        value = body.get("value")
        settings.put(key, value)
        # Apply runtime side-effects
        if key == "feedback_mode":
            core.feedback.mode = value
        core.event_bus.publish("settings.changed", {"key": key, "value": value})
        return {"ok": True}

    @app.get("/api/profiles")
    async def get_profiles():
        import settings
        return {
            "profiles": settings.list_profiles(),
            "active": settings.active_profile(),
        }

    @app.post("/api/profiles/{name}/load")
    async def load_profile(name: str):
        import settings
        settings.load_profile(name)
        core.event_bus.publish("settings.profile_changed", {"name": name})
        return {"ok": True}

    @app.post("/api/profiles/{name}/save")
    async def save_profile(name: str):
        import settings
        settings.save_profile(name)
        return {"ok": True}

    @app.post("/api/plugins/{name}/toggle")
    async def toggle_plugin(name: str):
        import settings
        is_loaded = name in core.plugin_manager.enabled
        if is_loaded:
            try:
                core.plugin_manager.unload_plugin(name)
            except Exception:
                pass
        else:
            for info in core.plugin_manager.discover():
                if info["name"] == name:
                    try:
                        core.plugin_manager.load_plugin(info)
                    except Exception as exc:
                        return JSONResponse({"error": str(exc)}, 500)
                    break
        settings.put("enabled_plugins", list(core.plugin_manager.enabled))
        core.event_bus.publish("plugins.changed", {
            "name": name, "enabled": name in core.plugin_manager.enabled,
        })
        return {"ok": True, "enabled": name in core.plugin_manager.enabled}

    # -- Voice Scribe -------------------------------------------------

    @app.get("/api/voice-scribe/state")
    async def get_voice_scribe_state():
        vs = core.plugin_manager.plugins.get("Voice Scribe")
        if not vs:
            return JSONResponse({"active": False, "status": "Plugin not loaded"}, 200)
        try:
            prompts = []
            for p in getattr(vs, '_prompt_list', []):
                system = p.get("system", "")
                prompts.append({
                    "pad": p.get("pad", 0),
                    "label": p.get("label", "?"),
                    "system": system,
                })
            return {
                "active": getattr(vs, '_active', False),
                "recording": getattr(vs, '_recording', False),
                "processing": getattr(vs, '_is_processing', lambda: False)(),
                "status": getattr(vs, '_status', 'Idle'),
                "last_original": getattr(vs, '_last_original', ''),
                "last_result": getattr(vs, '_last_result', ''),
                "last_prompt_label": getattr(vs, '_last_prompt_label', ''),
                "chat_model": getattr(vs, 'chat_model', ''),
                "transcription_model": getattr(vs, 'transcription_model', ''),
                "prompts": prompts,
                "chat_history_length": len(getattr(vs, '_chat_history', [])),
                "mic_device": getattr(vs, 'mic_device', None),
                "whisper_prompt": getattr(vs, '_whisper_prompt', ''),
            }
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, 500)

    @app.post("/api/voice-scribe/new-chat")
    async def voice_scribe_new_chat():
        vs = core.plugin_manager.plugins.get("Voice Scribe")
        if not vs:
            return JSONResponse({"error": "Plugin not loaded"}, 404)
        try:
            vs._chat_history.clear()
            vs._pending_context.clear()
            vs._last_original = ""
            vs._last_result = ""
            vs._status = "New chat"
            core._runtime_log("VS", "New chat started", (100, 255, 150))
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, 500)

    @app.put("/api/voice-scribe/prompts")
    async def voice_scribe_save_prompts(body: dict):
        """Bulk save all prompts + whisper_prompt."""
        vs = core.plugin_manager.plugins.get("Voice Scribe")
        if not vs:
            return JSONResponse({"error": "Plugin not loaded"}, 404)
        try:
            prompt_list = body.get("prompts", [])
            whisper_prompt = body.get("whisper_prompt", "")
            # Update plugin state
            vs._whisper_prompt = whisper_prompt.strip()
            vs._prompt_list = []
            for p in prompt_list:
                entry = {
                    "pad": int(p.get("pad", 0)),
                    "label": p.get("label", "").strip(),
                }
                system = p.get("system", "").strip()
                if system:
                    entry["system"] = system
                vs._prompt_list.append(entry)
            vs._prompt_list.sort(key=lambda x: x["pad"])
            # Persist and reload
            vs._save_prompts_to_file()
            vs._load_prompts()
            core._runtime_log("VS", "Prompts saved from Web UI", (80, 255, 120))
            return {"ok": True, "count": len(vs._prompt_list)}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, 500)

    @app.post("/api/voice-scribe/prompts")
    async def voice_scribe_add_prompt(body: dict):
        """Add a single prompt."""
        vs = core.plugin_manager.plugins.get("Voice Scribe")
        if not vs:
            return JSONResponse({"error": "Plugin not loaded"}, 404)
        try:
            label = body.get("label", "New Prompt").strip()
            system = body.get("system", "").strip()
            # Find next free pad number
            used_pads = {p.get("pad", 0) for p in vs._prompt_list}
            pad = 1
            while pad in used_pads:
                pad += 1
            entry = {"pad": pad, "label": label}
            if system:
                entry["system"] = system
            vs._prompt_list.append(entry)
            vs._prompt_list.sort(key=lambda x: x["pad"])
            vs._save_prompts_to_file()
            vs._load_prompts()
            core._runtime_log("VS", f"Prompt added: {label} (pad {pad})", (80, 255, 120))
            return {"ok": True, "pad": pad}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, 500)

    @app.delete("/api/voice-scribe/prompts/{pad}")
    async def voice_scribe_delete_prompt(pad: int):
        """Delete a prompt by pad number."""
        vs = core.plugin_manager.plugins.get("Voice Scribe")
        if not vs:
            return JSONResponse({"error": "Plugin not loaded"}, 404)
        try:
            before = len(vs._prompt_list)
            vs._prompt_list = [p for p in vs._prompt_list if p.get("pad") != pad]
            if len(vs._prompt_list) == before:
                return JSONResponse({"error": f"Pad {pad} not found"}, 404)
            vs._save_prompts_to_file()
            vs._load_prompts()
            core._runtime_log("VS", f"Prompt deleted: pad {pad}", (255, 180, 80))
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, 500)

    # -- Operations (async background tasks) --------------------------

    @app.post("/api/ops/start")
    async def start_operation(body: dict):
        op_type = body.get("op_type")
        params = body.get("params", {})
        if not op_type:
            return JSONResponse({"error": "op_type required"}, 400)
        try:
            op = core.op_manager.start(op_type, params)
            return op.to_dict()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.get("/api/ops")
    async def list_operations():
        return core.op_manager.get_all()

    @app.get("/api/ops/{op_id}")
    async def get_operation(op_id: str):
        op = core.op_manager.get(op_id)
        if not op:
            return JSONResponse({"error": "not found"}, 404)
        return op.to_dict()

    @app.post("/api/ops/{op_id}/cancel")
    async def cancel_operation(op_id: str):
        if not core.op_manager.cancel(op_id):
            return JSONResponse({"error": "not found or already finished"}, 404)
        return {"ok": True}

    # -- WebSocket ----------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        sub = core.event_bus.subscribe()
        try:
            state = core.get_state_snapshot()
            await ws.send_json({
                "type": "response",
                "id": "handshake",
                "status": "ok",
                "payload": state,
            })

            while True:
                msg = await sub.get()
                await ws.send_json(msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            core.event_bus.unsubscribe(sub)

    # -- Startup / Shutdown -------------------------------------------

    @app.on_event("startup")
    async def on_startup():
        log.info("Backend starting up...")
        core.event_bus.set_loop(asyncio.get_running_loop())
        core.start_services()
        log.info("Backend ready")

    @app.on_event("shutdown")
    async def on_shutdown():
        log.info("Backend shutting down...")
        core.shutdown()
        log.info("Backend shutdown complete")

    # -- Static files (production build) ------------------------------

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    return app
