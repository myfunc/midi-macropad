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

    # ── Global error handler ────────────────────────────────────────

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

    # ── REST routes ──────────────────────────────────────────────────

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
        note_a = body.get("note_a")
        note_b = body.get("note_b")
        if note_a is None or note_b is None:
            return JSONResponse({"error": "note_a and note_b required"}, 400)
        pad_a = core.mapper.registry.get_pad(note_a)
        pad_b = core.mapper.registry.get_pad(note_b)
        if (pad_a and pad_a.locked) or (pad_b and pad_b.locked):
            return JSONResponse({"error": "Cannot swap locked pads"}, 409)
        with core._lock:
            core.mapper.swap_pads(note_a, note_b)
        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    @app.patch("/api/pads/{note}")
    async def update_pad(note: int, body: dict):
        """Update pad label, hotkey, and/or action."""
        pad = core.mapper.registry.get_pad(note)
        if not pad:
            return JSONResponse({"error": f"Pad {note} not found"}, 404)
        label = body.get("label", pad.label)
        hotkey = body.get("hotkey", pad.hotkey)
        action_data = body.get("action")
        # Build action dict from current pad or from body
        if action_data is None:
            action_dict = {"type": pad.action_type}
            action_dict.update(pad.action_data)
        else:
            action_dict = action_data
        log.info("PATCH pad %d: label=%r hotkey=%r action=%r", note, label, hotkey, action_dict)
        with core._lock:
            core.mapper.update_pad(note, label, action_dict, hotkey=hotkey)
            core.hotkeys.reload_bindings(core.mapper)
        # Verify hotkey was applied
        updated = core.mapper.registry.get_pad(note)
        log.info("PATCH pad %d result: hotkey=%r action_type=%r", note, updated.hotkey if updated else '?', updated.action_type if updated else '?')
        core.event_bus.publish("pads.updated", {
            "pads": core.get_state_snapshot()["pads"],
        })
        return {"ok": True}

    @app.delete("/api/pads/{note}/action")
    async def clear_pad_action(note: int):
        """Clear pad action and hotkey."""
        with core._lock:
            core.mapper.update_pad(note, "", {"type": ""}, hotkey="")
            core.hotkeys.reload_bindings(core.mapper)
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

    # ── Panel presets ─────────────────────────────────────────────────

    @app.get("/api/panels/{panel_id}/preset")
    async def get_panel_preset(panel_id: str):
        info = core.get_panel_preset(panel_id)
        if info is None:
            return JSONResponse({"error": f"Panel '{panel_id}' not found"}, 404)
        return info

    @app.post("/api/panels/{panel_id}/preset")
    async def set_panel_preset(panel_id: str, body: dict):
        preset_name = body.get("preset")
        if not preset_name:
            return JSONResponse({"error": "preset name required"}, 400)
        ok = core.set_panel_preset(panel_id, preset_name)
        if not ok:
            return JSONResponse({"error": f"Preset '{preset_name}' not found or invalid panel"}, 404)
        return {"ok": True, "panel": panel_id, "preset": preset_name}

    @app.put("/api/panels/{panel_id}/order")
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

    @app.get("/api/knobs/catalog")
    async def get_knob_catalog():
        """Aggregated knob action catalog from all enabled plugins.

        Returns ``{"core": [...], "plugins": {plugin_name: [...]}}``.
        The ``core`` list describes built-in knob action types that don't
        route through a plugin (e.g. volume targets).
        """
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

    # ── Settings & Profiles ────────────────────────────────────────────

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

    # ── Voice Scribe ─────────────────────────────────────────────────

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

    # ── Operations (async background tasks) ──────────────────────────

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

    # ── WebSocket ────────────────────────────────────────────────────

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

    # ── Startup / Shutdown ───────────────────────────────────────────

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

    # ── Static files (production build) ──────────────────────────────

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    return app
