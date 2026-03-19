# 2026-03-19 — Voice Feedback Cues and Hard Cancel

## Goal

Record the current direction for user-facing feedback in `MIDI Macropad`: how notification sounds should work on the controller itself, and how `Voice Scribe` cancellation now behaves.

## Context

- **Controller**: Akai `MPK Mini Play` (OG)
- **Feedback transport**: host-sent MIDI out to the controller's internal GM synth
- **Related research**:
  - `2026-03-18_led-feedback-hardware.md`
  - `2026-03-18_midi-theme-experiments.md`

## Findings

### 1. MIDI device feedback is the right feedback path on this hardware

The LED research already established that `MPK Mini Play` does not expose practical host-controlled LED feedback. That makes device-side MIDI playback the best native feedback channel available in the current setup.

Instead of playing host-audio beeps, the project now routes semantic cue IDs through a shared `FeedbackService`, which opens the controller's MIDI output port and sends short phrases to the built-in synth.

### 2. Feedback should stay in the core runtime, not inside plugin-specific system calls

`Voice Scribe` still needs direct integrations for microphone capture, clipboard access, keyboard paste, and OpenAI API requests. But feedback is now intentionally separated from those responsibilities.

The plugin emits cue IDs such as:

- `voice.record_start`
- `voice.context_added`
- `voice.processing_start`
- `voice.done`
- `voice.cancel_requested`
- `voice.cancelled`
- `voice.warn`
- `voice.error`

This keeps the plugin API cleaner and lets other workflows reuse the same feedback channel later.

### 3. `Voice Scribe` cancel is now a hard cancel at the application level

The earlier design allowed a canceled job to keep running far enough to potentially finish with stale UI state or a stale paste.

The current behavior uses token-based invalidation:

- `Cancel` immediately stops recording if it is active
- the current processing token is invalidated
- stale jobs can no longer paste text
- stale jobs can no longer set final success status
- a new turn can start immediately

Important limitation: this is an application-level hard cancel. It prevents stale results from mutating the app state, but it does not guarantee that an already-issued HTTP request is physically aborted at the socket level by the SDK.

### 4. The cue language should be musical, louder, and realistic on `MPK Mini Play`

The original idea of tiny synthetic blips was replaced with something more intentional:

- use lower and midrange notes rather than bright high-register pings
- prefer simple, readable intervals and short arpeggiated fragments
- raise `CC7` volume and `CC11` expression so cues survive the device mix
- use GM guitar-family patches for the main musical phrases
- use drum channel hits only for warning and error cues that need to cut through clearly

Current timbre direction:

- `program 25` for more melodic steel-string style arpeggios
- `program 26` and `27` for alternate guitar-like transitions
- `program 28` for drier muted cancel phrases
- channel `9` percussion for warning/error emphasis

## Practical Outcome

The feedback system should now be understood as:

- **default actions** -> shared MIDI cue phrases on the device
- **Voice Scribe states** -> dedicated MIDI motifs on the device
- **plugin integration** -> semantic cue emission through the host runtime service
- **cancel behavior** -> hard cancel for application state, even if the underlying SDK request may still unwind in the background

## Follow-up

If the cue set is revisited again, the next likely refinement is not a transport change but a tuning pass:

- test the exact GM guitar patches on real hardware
- rebalance `CC7` / `CC11` if the mix is still too quiet
- move cue definitions into a config file if live iteration becomes frequent
