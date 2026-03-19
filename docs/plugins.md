# Plugin Catalog

Plugins are the extension layer of `MIDI Macropad`. Each plugin can intercept MIDI events, expose custom pad labels, add its own UI panels, and receive shared runtime services from the host app.

Today that shared runtime surface is intentionally small. The key example is the feedback service: plugins emit semantic cue IDs and the core routes them to the controller's MIDI output instead of encouraging direct audio or system-side notification hacks inside each plugin.

## Current Plugins

| Plugin | Role |
|--------|------|
| `Voice Scribe` | Voice-driven writing assistant for Russian speech, English output, and context-aware generation |
| `Sample Player` | Sample playback engine for turning pads into a small performance sampler |

## Voice Scribe

`Voice Scribe` is the main showcase plugin.

It is designed for a very specific workflow: think out loud in Russian, then instantly paste clean English into the app you are already using.

### What it can do

- Record from the selected microphone at the press of a pad
- Transcribe Russian speech with Whisper
- Rewrite or translate the result with GPT
- Paste the final text directly at the cursor
- Save reusable prompt styles in `prompts.toml`
- Let the UI edit prompt labels and system prompts without touching code
- Capture selected text as extra context before generation
- Run a persistent "Speak" mode with chat history and saved conversation logs
- Hard-cancel recording or processing from a dedicated pad so stale jobs cannot paste text later
- Emit device-side MIDI cues for `record_start`, `context_added`, `processing_start`, `done`, `cancel`, `warn`, and `error`

### Why it matters

This plugin is the reason the project feels different from a normal macro pad.

Most macro devices stop at shortcuts. `Voice Scribe` turns the controller into a writing instrument: one button to collect context, one button to speak, and a polished result appears where you are already working. It is especially useful for bilingual communication, quick replies, summaries, and drafting with less friction.

### Current pad concepts

The default prompt set includes a mix of generation styles and control actions:

- `Professional`
- `Professional Summary`
- `Socials`
- `Summary`
- `New Chat`
- `Context`
- `Speak`
- `Cancel`

### Configuration notes

- API key can come from the plugin settings UI, `.env`, or the plugin-local key file
- microphone selection is stored per plugin
- prompts are editable in both `prompts.toml` and the built-in Prompt Editor
- chat logs are stored in `plugins/voice_scribe/chats/`
- feedback cues use the main controller MIDI output, not host audio playback

## Sample Player

`Sample Player` turns the pad grid into a compact WAV sampler.

### What it can do

- load sample packs from `plugins/sample_player/packs/`
- trigger samples with velocity-sensitive volume
- play multiple voices at once
- switch packs in the UI
- control plugin volume from the UI or MIDI knob input
- stay scoped to `Sound Pads` mode so it does not steal pads from general workflows

### Pack format

Each pack is a folder with a `pack.toml` manifest and referenced WAV files. This makes it easy to create drum kits, soundboards, or performance-oriented pad sets.
