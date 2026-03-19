# 2026-03-18 — LED Feedback: Hardware Research

## Goal

Determine whether the Akai MPK Mini Play supports controlling pad/button LEDs from host software via MIDI output.

## Device

- **Model**: Akai MPK Mini Play (OG, not MK3)
- **MCU**: STMicroelectronics STM32F401 ARM
- **Synth chip**: Dream S.A.S. SAM2635 (16-channel GM/GS)
- **SysEx device ID**: `0x44` (`F0 47 7F 44 ...`)
- **Internal architecture**: ARM receives USB MIDI → routes to SAM2635 via USART

## How LED Control Works on Other Akai Models

### MPK Mini MK1 / MK2 (works)

Send `note_on` on **MIDI channel 0** back to the device:

| Note | LED |
|------|-----|
| 0 | Arpeggiator ON/OFF |
| 3 | Arp (alternate source) |
| 4 | Tap Tempo |
| 5 | Octave Down |
| 6 | Octave Up |
| 7 | Full Level |
| 9–16 | Pads 1–8 |
| 25 | Bank A/B |
| 26 | CC |
| 27 | Prog Change |

- `velocity = 127` → LED ON
- `velocity = 0` (or non-127) → LED OFF
- These note_on events only affect lighting, not internal button state.

### MPK Mini MK3 (does NOT work)

Confirmed by multiple users — the MK3 firmware dropped LED control entirely.

### Akai APC Mini (works, different protocol)

Full LED control via note_on with color-coded velocities. Documented protocol.

### Novation Launchpad (works, full RGB)

SysEx-based LED protocol. Full RGB color control per pad.

## Testing on MPK Mini Play

### What We Tried

1. **Notes 9–16, channel 0** (classic MPK Mini mapping) — heard synth sounds, no LEDs
2. **Notes 16–23, channel 0** (echo mapping, same as pad input) — heard synth sounds, no LEDs
3. **Notes 9–16, channel 9** (drum channel) — nothing
4. **Notes 16–23, channel 9** — nothing
5. **Sweep notes 0–32, channel 0** — rising synth sound, no LEDs
6. **Body button notes (0, 4, 5, 6, 7, 26, 27), channel 0** — piano sounds, no LEDs
7. **Synth muted via CC#7=0** — not tested separately (Internal Sounds already OFF)
8. **Internal Sounds button OFF** — all tests above were done with Internal Sounds OFF

### Result

**No LED response to any MIDI output message.** The ARM firmware does not implement LED control via MIDI note_on, unlike the original MPK Mini.

## Root Cause Analysis

The MPK Mini Play's ARM firmware routes all incoming MIDI messages to the Dream SAM2635 synthesizer chip (even with Internal Sounds OFF — the chip still processes them, just no audio output to speaker). The firmware **does not** intercept low-note note_on messages for LED control the way the original MPK Mini does.

Additionally:
- ARM firmware **filters all SysEx** except Akai-specific (`F0 47 7F 44 ...`)
- Known Akai SysEx function codes are for preset management only (get/set program, knob positions)
- No documented or community-discovered SysEx command for LED control exists
- The [akai-mpk-mini-play-guts](https://github.com/severak/akai-mpk-mini-play-guts) reverse-engineering project found no LED control path

## Conclusion

**LED feedback from host software is not possible on the MPK Mini Play.** This is a firmware limitation — the feature was never implemented in this model's ARM code.

The LED infrastructure in the codebase (`led_controller.py`, plugin hooks) is fully functional and ready for controllers that support it. Set `[led] enabled = true` in `config.toml` if switching to a supported controller.

## References

- [AKAI MPKmini LED control gist (ericfont)](https://gist.github.com/ericfont/5d349a5922293173347ff86e4fe2cb8c) — original MPK Mini LED note mapping
- [MPK Mini Play MIDI over USB implementation (sandsoftwaresound)](https://sandsoftwaresound.net/akai-mpk-mini-play-midi-implementation/) — detailed analysis of ARM→SAM2635 routing, SysEx filtering
- [MPK Mini Play firmware mods (severak)](https://github.com/severak/akai-mpk-mini-play-guts) — ARM firmware reverse-engineering attempt
- [mpd-utils: MPK Mini Play support (mungewell)](https://github.com/mungewell/mpd-utils/issues/1) — SysEx protocol analysis, preset dump format
- [AKAI MPKmini technical details (tranzoa.net)](https://www.tranzoa.net/~alex/blog/?p=1109) — comprehensive LED note map and SysEx reverse engineering for original MPK Mini
- [Cycling '74 forum: MPK Mini LED control](https://cycling74.com/forums/akai-mpk-mini-send-get-signals-to-light-buttons) — community discussion, MaxMSP approach
- [Web MIDI MPK Mini editor (gljubojevic)](https://github.com/gljubojevic/akai-mpk-mini-editor) — reverse-engineered SysEx preset protocol
