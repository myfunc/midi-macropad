# 2026-03-18 — MIDI Theme Experiments on MPK Mini Play

## Goal

Capture the ad hoc MIDI playback experiments from this chat and preserve the practical findings in `researches/` instead of keeping a standalone experiment script in the project root.

## Context

- **Device**: Akai MPK Mini Play (OG)
- **MIDI output used**: `MPK mini play 1`
- **Python**: `3.12.0`
- **Libraries**: `mido`, `python-rtmidi`, `tomllib`
- **Config device hint**: `config.toml` -> `[device].name = "MPK mini play"`
- **Related prior research**: `2026-03-18_led-feedback-hardware.md`

## Findings

### 1. Basic MIDI output playback works

The device accepted host-sent MIDI output and played internal General MIDI sounds through the exposed output port:

```bash
python midi_channel_showcase.py --list
python midi_channel_showcase.py --mode showcase
python midi_channel_showcase.py --mode theme --theme-variant clean
python midi_channel_showcase.py --mode theme --theme-variant heavy
```

Detected ports during testing:

| Port | Result |
|------|--------|
| `Microsoft GS Wavetable Synth 0` | Available but not target hardware |
| `MPK mini play 1` | Used successfully for all tests |

### 2. Channel showcase worked reliably

A standalone script successfully:

- auto-detected the MIDI output port from `config.toml`
- sent `program_change` messages on melodic channels
- used channel 10 for drums
- sent `all notes off` at the end to avoid stuck notes

Observed result: the script completed successfully and the device played all programmed parts.

### 3. Themed composition experiments were possible, but the result remained exploratory

The script evolved from a simple channel sweep into two arranged variants:

- `clean` — lighter arcade-rock arrangement
- `heavy` — denser synth-rock arrangement

Both versions used multiple parts:

- lead
- counter line
- rhythm
- bass
- pad
- drums

Structural ideas tested:

| Section | Intent |
|---------|--------|
| Light section | More open, game-level feel |
| Transition | Descending tension / handoff |
| Dark section | More minor, more pressure, louder drums |

### 4. Dynamics and drum audibility needed explicit MIDI balancing

The MPK Mini Play's internal synth did not naturally preserve the intended arrangement balance. To improve the result, the experiment had to:

- reduce melodic channel base volumes
- raise drum channel volume aggressively
- add per-section `CC11` expression automation
- make dark-section drums denser and more accented

Tested dynamics approach:

| Technique | Why |
|-----------|-----|
| `CC7` channel volume | Set coarse per-track balance |
| `CC11` expression | Add section-by-section crescendos and drops |
| Higher drum velocities | Make kick/snare survive the device mix |
| Simpler timbres | Reduce "toy-like" character on this hardware |

### 5. Negative result: arrangement quality on this hardware is limited

Even though the MIDI scripting worked technically, the MPK Mini Play's built-in sound set constrained the emotional range:

- bright patches could sound childish
- subtle orchestration choices did not always translate
- drums were easy to lose unless explicitly over-emphasized
- "dramatic" intent required heavy simplification and exaggerated MIDI dynamics

Practical conclusion: the hardware is usable for quick MIDI playback experiments, but not ideal for nuanced composition mockups.

## Conclusion

The MIDI experiments from this chat were successful as a **technical playback test** and as a **proof of concept for scripted multi-track arrangements** on the MPK Mini Play. However, they remain exploratory and are not a good fit for the main project root.

Practical outcome:

- keep the knowledge
- keep the commands and tested constraints
- remove the temporary experiment script from the main project surface

If this work is revisited later, create a new dated research entry and, if needed, rebuild the script in a more intentional tools/experiments area instead of the root.

## References

- [Super Meat Boy soundtrack](https://dbsoundworks.bandcamp.com/album/super-meat-boy-soundtrack) — soundtrack listing and genre framing as electronic/rock/metal
- [PC Gamer interview with Danny Baranowsky](https://www.pcgamer.com/behind-the-music-interview-with-super-meat-boy-composer-danny-baranowsky/) — notes on bombastic synth-driven writing style
- [MPK Mini Play MIDI over USB implementation](https://sandsoftwaresound.net/akai-mpk-mini-play-midi-implementation/) — hardware/MIDI routing context for the device
