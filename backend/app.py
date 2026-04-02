"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core import AppCore

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app(core: AppCore) -> FastAPI:
    app = FastAPI(title="MIDI Macropad", version="1.0.0")
    app.state.core = core

    # CORS for dev mode (Vite on :5173)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
        return {"ok": True}

    @app.get("/api/pads")
    async def get_pads():
        snapshot = core.get_state_snapshot()
        return snapshot["pads"]

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
        ok = core.mapper.registry.swap_pads(note_a, note_b)
        if ok:
            core.mapper.swap_pads(note_a, note_b)
        return {"ok": ok}

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
        if 0 <= index < len(core.config.pad_presets):
            try:
                core.mapper.set_preset(index)
                import settings
                settings.put("preset_index", index)
                # These may touch DearPyGui internals in plugins — wrap safely
                try:
                    core.plugin_manager.on_mode_changed(core.mapper.current_preset.name)
                except Exception:
                    pass
                try:
                    core.plugin_manager.notify_preset_changed(core.mapper)
                except Exception:
                    pass
                core.event_bus.publish("preset.changed", {
                    "index": index,
                    "name": core.mapper.current_preset.name,
                    "pads": core.get_state_snapshot()["pads"],
                })
                return {"ok": True, "name": core.mapper.current_preset.name}
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, 500)
        return JSONResponse({"error": "invalid index"}, 400)

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

    # ── WebSocket ────────────────────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        sub = core.event_bus.subscribe()
        try:
            # Send initial state
            state = core.get_state_snapshot()
            await ws.send_json({
                "type": "response",
                "id": "handshake",
                "status": "ok",
                "payload": state,
            })

            # Fan-out events to this client
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
        core.event_bus.set_loop(asyncio.get_running_loop())
        core.start_services()

    @app.on_event("shutdown")
    async def on_shutdown():
        core.shutdown()

    # ── Static files (production build) ──────────────────────────────

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    return app
