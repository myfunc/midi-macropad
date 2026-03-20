"""Performance template plugin for live MIDI sketching on MPK Mini Play."""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import mido
import toml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base import Plugin
import settings


PAD_NOTE_MIN = 16
PAD_NOTE_MAX = 23
PERFORMANCE_CHANNELS = (1, 2, 3, 9)


@dataclass(frozen=True)
class PhraseEvent:
    offset_ms: int
    notes: tuple[int, ...]
    velocity: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class PhraseSlot:
    input_note: int
    label: str
    channel: int
    program: int | None
    velocity: int
    duration_ms: int
    cue_id: str = ""
    events: tuple[PhraseEvent, ...] = ()


@dataclass(frozen=True)
class BeatEvent:
    step: int
    notes: tuple[int, ...]
    velocity: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class BeatLayer:
    input_note: int
    label: str
    channel: int
    program: int | None
    velocity: int
    duration_ms: int
    cue_on: str
    cue_off: str
    events: tuple[BeatEvent, ...] = ()


@dataclass(frozen=True)
class ChordBank:
    name: str
    slots: dict[int, PhraseSlot]


class PerformanceTemplatePlugin(Plugin):
    name = "Performance Template"
    version = "0.1.0"
    description = "Pad beat toggles plus key-based drums, riffs, and chord banks"
    mode_name = "Performance"

    def __init__(self):
        self.template_path = Path(__file__).with_name("template.toml")
        self._active = False
        self._running = False
        self._feedback_enabled = True
        self._state_lock = threading.Lock()
        self._sequencer_thread: threading.Thread | None = None
        self._beat_layers: dict[int, BeatLayer] = {}
        self._drum_slots: dict[int, PhraseSlot] = {}
        self._drop_slots: dict[int, PhraseSlot] = {}
        self._riff_slots: dict[int, PhraseSlot] = {}
        self._chord_banks: list[ChordBank] = []
        self._selected_bank_name = ""
        self._active_beats: set[int] = set()
        self._tempo_bpm = 92
        self._steps_per_bar = 16
        self._key_low_note = 48
        self._key_high_note = 72

    # -- lifecycle ---------------------------------------------------------

    def on_load(self, config: dict) -> None:
        template_name = config.get("template", "template.toml")
        self.template_path = Path(__file__).with_name(template_name)
        self._feedback_enabled = bool(settings.get("performance_feedback_enabled", True))
        self.reload_template()
        self._running = True
        self._sequencer_thread = threading.Thread(target=self._sequencer_loop, daemon=True)
        self._sequencer_thread.start()

    def on_unload(self) -> None:
        self._running = False
        if self._sequencer_thread is not None:
            self._sequencer_thread.join(timeout=1.0)
        self._stop_performance()

    def on_mode_changed(self, mode_name: str) -> None:
        was_active = self._active
        self._active = mode_name == self.mode_name
        if was_active and not self._active:
            self._stop_performance()

    # -- event hooks -------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active:
            return False

        if note in self._beat_layers:
            self._toggle_beat(note)
            return True

        slot = self._slot_for_note(note)
        if slot is None:
            return False
        self._play_phrase(slot, velocity)
        return True

    def on_pad_release(self, note: int) -> bool:
        if not self._active:
            return False
        return note in self._all_slots()

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        labels: dict[int, str] = {}
        with self._state_lock:
            for note, beat in self._beat_layers.items():
                suffix = " *" if note in self._active_beats else ""
                labels[note] = f"{beat.label}{suffix}"
            for note, slot in self._drum_slots.items():
                labels[note] = slot.label
            for note, slot in self._drop_slots.items():
                labels[note] = slot.label
            for note, slot in self._riff_slots.items():
                labels[note] = slot.label
            for note, slot in self._current_chord_slots().items():
                labels[note] = slot.label
        return labels

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._active:
            return None
        with self._state_lock:
            bank = self._selected_bank_name or "None"
            active = [self._beat_layers[note].label for note in sorted(self._active_beats)]
            beat_text = ", ".join(active[:3])
            if len(active) > 3:
                beat_text += "..."
            if not beat_text:
                beat_text = "idle"
            return (
                f"Performance {self._tempo_bpm} BPM | {bank} | Beats: {beat_text}",
                (120, 220, 140),
            )

    # -- UI ----------------------------------------------------------------

    def build_ui(self, parent_tag: str) -> None:
        self._build_main_controls(parent_tag, scope="sidebar")

    def build_properties(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("Performance Settings", parent=parent_tag, color=(100, 180, 255))
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)
        dpg.add_checkbox(
            label="Sound Feedback",
            default_value=self._feedback_enabled,
            callback=lambda sender, app_data: self.set_feedback_enabled(bool(app_data)),
            parent=parent_tag,
        )
        dpg.add_spacer(height=6, parent=parent_tag)
        dpg.add_text(
            "On: musical cues play for Performance actions.\nOff: only the played notes and patterns remain.",
            parent=parent_tag,
            wrap=260,
            color=(150, 150, 160),
        )

    def register_windows(self) -> list[dict]:
        return [
            {"id": "performance_live", "title": "Performance", "default_open": True},
        ]

    def build_window(self, window_id: str, parent_tag: str) -> None:
        if window_id != "performance_live":
            return
        self._build_main_controls(parent_tag, scope="window")

    # -- template loading ---------------------------------------------------

    def reload_template(self) -> None:
        data = toml.load(str(self.template_path))
        perf = data.get("performance", {})

        beat_layers = {
            item["input_note"]: self._parse_beat_layer(item)
            for item in data.get("beats", [])
        }
        drum_slots = {
            item["input_note"]: self._parse_phrase_slot(item)
            for item in data.get("drums", [])
        }
        drop_slots = {
            item["input_note"]: self._parse_phrase_slot(item)
            for item in data.get("drops", [])
        }
        riff_slots = {
            item["input_note"]: self._parse_phrase_slot(item)
            for item in data.get("riffs", [])
        }
        chord_banks = []
        for bank_data in data.get("chord_banks", []):
            slots = {
                item["input_note"]: self._parse_phrase_slot(item)
                for item in bank_data.get("slots", [])
            }
            chord_banks.append(ChordBank(name=bank_data["name"], slots=slots))

        default_bank = perf.get("default_chord_bank", "")
        with self._state_lock:
            self._tempo_bpm = int(perf.get("tempo_bpm", self._tempo_bpm))
            self._steps_per_bar = int(perf.get("steps_per_bar", self._steps_per_bar))
            self._key_low_note = int(perf.get("key_low_note", self._key_low_note))
            self._key_high_note = int(perf.get("key_high_note", self._key_high_note))
            self._beat_layers = beat_layers
            self._drum_slots = drum_slots
            self._drop_slots = drop_slots
            self._riff_slots = riff_slots
            self._chord_banks = chord_banks
            if default_bank and any(bank.name == default_bank for bank in chord_banks):
                self._selected_bank_name = default_bank
            elif chord_banks and self._selected_bank_name not in {bank.name for bank in chord_banks}:
                self._selected_bank_name = chord_banks[0].name

    def set_chord_bank(self, bank_name: str) -> None:
        with self._state_lock:
            if any(bank.name == bank_name for bank in self._chord_banks):
                self._selected_bank_name = bank_name

    def set_tempo(self, bpm: int) -> None:
        with self._state_lock:
            self._tempo_bpm = max(40, int(bpm))

    def set_feedback_enabled(self, enabled: bool) -> None:
        with self._state_lock:
            self._feedback_enabled = bool(enabled)
        settings.put("performance_feedback_enabled", self._feedback_enabled)

    # -- parsing helpers ----------------------------------------------------

    def _parse_phrase_slot(self, data: dict) -> PhraseSlot:
        events = tuple(
            PhraseEvent(
                offset_ms=int(event.get("offset_ms", 0)),
                notes=tuple(int(note) for note in event.get("notes", [])),
                velocity=event.get("velocity"),
                duration_ms=event.get("duration_ms"),
            )
            for event in data.get("events", [])
        )
        return PhraseSlot(
            input_note=int(data["input_note"]),
            label=str(data["label"]),
            channel=int(data.get("channel", 0)),
            program=data.get("program"),
            velocity=int(data.get("velocity", 100)),
            duration_ms=int(data.get("duration_ms", 180)),
            cue_id=str(data.get("cue_id", "")),
            events=events,
        )

    def _parse_beat_layer(self, data: dict) -> BeatLayer:
        events = tuple(
            BeatEvent(
                step=int(event["step"]),
                notes=tuple(int(note) for note in event.get("notes", [])),
                velocity=event.get("velocity"),
                duration_ms=event.get("duration_ms"),
            )
            for event in data.get("events", [])
        )
        return BeatLayer(
            input_note=int(data["input_note"]),
            label=str(data["label"]),
            channel=int(data.get("channel", 9)),
            program=data.get("program"),
            velocity=int(data.get("velocity", 96)),
            duration_ms=int(data.get("duration_ms", 90)),
            cue_on=str(data.get("cue_on", "action.toggle_on")),
            cue_off=str(data.get("cue_off", "action.toggle_off")),
            events=events,
        )

    # -- playback -----------------------------------------------------------

    def _toggle_beat(self, note: int) -> None:
        with self._state_lock:
            beat = self._beat_layers.get(note)
            if beat is None:
                return
            if note in self._active_beats:
                self._active_beats.remove(note)
                cue_id = beat.cue_off
            else:
                self._active_beats.add(note)
                cue_id = beat.cue_on
        self._emit_feedback(cue_id)

    def _play_phrase(self, slot: PhraseSlot, velocity: int) -> None:
        self._emit_feedback(slot.cue_id)
        thread = threading.Thread(
            target=self._run_phrase,
            args=(slot, velocity),
            daemon=True,
        )
        thread.start()

    def _run_phrase(self, slot: PhraseSlot, velocity: int) -> None:
        started = time.perf_counter()
        for event in slot.events:
            target_time = started + (event.offset_ms / 1000.0)
            delay = target_time - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            event_velocity = max(1, min(127, event.velocity or self._scaled_velocity(slot.velocity, velocity)))
            event_duration = max(30, int(event.duration_ms or slot.duration_ms))
            self._play_note_group(
                channel=slot.channel,
                program=slot.program,
                notes=event.notes,
                velocity=event_velocity,
                duration_ms=event_duration,
            )

    def _play_note_group(
        self,
        *,
        channel: int,
        program: int | None,
        notes: tuple[int, ...],
        velocity: int,
        duration_ms: int,
    ) -> None:
        if not notes:
            return
        feedback = self._runtime_services.get("feedback")
        if feedback is None:
            return

        def worker():
            messages = []
            if program is not None and channel != 9:
                messages.append(mido.Message("program_change", channel=channel, program=int(program)))
            messages.extend(
                mido.Message("note_on", channel=channel, note=int(note), velocity=int(velocity))
                for note in notes
            )
            feedback.send_midi(*messages)
            time.sleep(duration_ms / 1000.0)
            feedback.send_midi(*[
                mido.Message("note_off", channel=channel, note=int(note), velocity=0)
                for note in notes
            ])

        threading.Thread(target=worker, daemon=True).start()

    def _sequencer_loop(self) -> None:
        step = 0
        next_tick = time.perf_counter()
        while self._running:
            with self._state_lock:
                active = self._active
                tempo = max(40, self._tempo_bpm)
                steps_per_bar = max(1, self._steps_per_bar)
                active_beats = [self._beat_layers[note] for note in sorted(self._active_beats)]

            if not active or not active_beats:
                step = 0
                next_tick = time.perf_counter() + 0.05
                time.sleep(0.05)
                continue

            for beat in active_beats:
                for event in beat.events:
                    if event.step % steps_per_bar != step:
                        continue
                    self._play_note_group(
                        channel=beat.channel,
                        program=beat.program,
                        notes=event.notes,
                        velocity=int(event.velocity or beat.velocity),
                        duration_ms=int(event.duration_ms or beat.duration_ms),
                    )

            step = (step + 1) % steps_per_bar
            next_tick += 60.0 / tempo / 4.0
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.perf_counter()

    def _stop_performance(self) -> None:
        feedback = self._runtime_services.get("feedback")
        with self._state_lock:
            self._active_beats.clear()
        if feedback is not None:
            feedback.all_notes_off(*PERFORMANCE_CHANNELS)

    # -- lookup helpers -----------------------------------------------------

    def _slot_for_note(self, note: int) -> PhraseSlot | None:
        slots = self._all_slots()
        return slots.get(note)

    def _all_slots(self) -> dict[int, PhraseSlot]:
        slots = dict(self._drum_slots)
        slots.update(self._drop_slots)
        slots.update(self._riff_slots)
        slots.update(self._current_chord_slots())
        return slots

    def _current_chord_slots(self) -> dict[int, PhraseSlot]:
        for bank in self._chord_banks:
            if bank.name == self._selected_bank_name:
                return bank.slots
        return self._chord_banks[0].slots if self._chord_banks else {}

    def _scaled_velocity(self, base_velocity: int, played_velocity: int) -> int:
        ratio = max(0.35, min(1.2, played_velocity / 100.0))
        return max(1, min(127, int(base_velocity * ratio)))

    def _emit_feedback(self, cue_id: str) -> None:
        with self._state_lock:
            enabled = self._feedback_enabled
        if enabled and cue_id:
            self.emit_feedback(cue_id)

    def _build_main_controls(self, parent_tag: str, scope: str) -> None:
        import dearpygui.dearpygui as dpg

        bank_names = [bank.name for bank in self._chord_banks]
        bpm_label_tag = f"perf_bpm_label_{scope}_{id(self)}"

        dpg.add_text("Performance Template", parent=parent_tag)
        dpg.add_text(
            "Pads 16-23 toggle beat layers. Keys 48-63 fire drums, drops, and riffs. "
            "Keys 64-72 use the selected chord bank.",
            parent=parent_tag,
            wrap=420 if scope == "window" else 260,
            color=(150, 150, 160),
        )
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_checkbox(
            label="Sound Feedback",
            default_value=self._feedback_enabled,
            callback=lambda sender, app_data: self.set_feedback_enabled(bool(app_data)),
            parent=parent_tag,
        )
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Chord Bank", parent=parent_tag)

        def on_bank_changed(sender, app_data):
            self.set_chord_bank(app_data)

        dpg.add_combo(
            bank_names,
            default_value=self._selected_bank_name or (bank_names[0] if bank_names else ""),
            callback=on_bank_changed,
            parent=parent_tag,
            width=320 if scope == "window" else 250,
        )

        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text(f"Tempo: {self._tempo_bpm} BPM", parent=parent_tag, tag=bpm_label_tag)

        def on_bpm_changed(sender, app_data):
            self.set_tempo(int(app_data))
            if dpg.does_item_exist(bpm_label_tag):
                dpg.set_value(bpm_label_tag, f"Tempo: {self._tempo_bpm} BPM")

        dpg.add_slider_int(
            default_value=self._tempo_bpm,
            min_value=70,
            max_value=160,
            callback=on_bpm_changed,
            parent=parent_tag,
            width=320 if scope == "window" else 250,
        )

        def on_reload():
            selected = self._selected_bank_name
            self.reload_template()
            if selected:
                self.set_chord_bank(selected)

        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_button(label="Reload Template", callback=on_reload, parent=parent_tag)

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)
        dpg.add_text("Default layout", parent=parent_tag, color=(100, 200, 255))
        for line in self._layout_lines():
            dpg.add_text(
                line,
                parent=parent_tag,
                wrap=420 if scope == "window" else 260,
                color=(170, 170, 180),
            )

    def _layout_lines(self) -> list[str]:
        with self._state_lock:
            return [
                f"Pads {PAD_NOTE_MIN}-{PAD_NOTE_MAX}: beat toggles",
                f"Keys {self._key_low_note}-{self._key_low_note + 7}: drums",
                f"Keys {self._key_low_note + 8}-{self._key_low_note + 11}: guitar drops",
                f"Keys {self._key_low_note + 12}-{self._key_low_note + 15}: riffs",
                f"Keys {self._key_high_note - 8}-{self._key_high_note}: 9 chord slots",
                f"Sound feedback: {'On' if self._feedback_enabled else 'Off'}",
                "Edit template.toml to swap sounds, phrases, and chord banks.",
            ]
