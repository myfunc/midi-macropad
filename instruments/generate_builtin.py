"""Generate built-in synth instruments (sine piano, electric piano, organ).

Run: python instruments/generate_builtin.py
Creates SFZ instruments with synthesized WAV samples — no downloads needed.
"""

import os
import struct
import math

SAMPLE_RATE = 44100
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def midi_to_freq(note: int) -> float:
    return 440.0 * (2 ** ((note - 69) / 12))


def write_wav(path: str, samples: list[float], sample_rate: int = SAMPLE_RATE):
    """Write mono 16-bit WAV file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = len(samples)
    data = b""
    for s in samples:
        clamped = max(-1.0, min(1.0, s))
        data += struct.pack("<h", int(clamped * 32767))

    # WAV header
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # chunk size
        f.write(struct.pack("<HHIIHh", 1, 1, sample_rate, sample_rate * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)


def generate_piano_tone(freq: float, duration: float = 2.0) -> list[float]:
    """Sine-based piano with harmonics and exponential decay."""
    n = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        decay = math.exp(-t * 3.0)
        # Fundamental + harmonics
        s = math.sin(2 * math.pi * freq * t) * 0.6
        s += math.sin(2 * math.pi * freq * 2 * t) * 0.25 * math.exp(-t * 4.0)
        s += math.sin(2 * math.pi * freq * 3 * t) * 0.1 * math.exp(-t * 5.0)
        s += math.sin(2 * math.pi * freq * 4 * t) * 0.05 * math.exp(-t * 6.0)
        samples.append(s * decay * 0.8)
    return samples


def generate_epiano_tone(freq: float, duration: float = 2.5) -> list[float]:
    """Electric piano — FM synthesis (DX7-style)."""
    n = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        decay = math.exp(-t * 2.5)
        mod_decay = math.exp(-t * 4.0)
        # FM: carrier + modulator
        mod = math.sin(2 * math.pi * freq * 14 * t) * 2.0 * mod_decay
        s = math.sin(2 * math.pi * freq * t + mod)
        # Add bell-like overtone
        s += math.sin(2 * math.pi * freq * 3 * t) * 0.15 * math.exp(-t * 5.0)
        samples.append(s * decay * 0.6)
    return samples


def generate_organ_tone(freq: float, duration: float = 2.0) -> list[float]:
    """Drawbar organ — additive synthesis."""
    n = int(SAMPLE_RATE * duration)
    # Drawbar levels (16', 5⅓', 8', 4', 2⅔', 2', 1⅗', 1⅓', 1')
    drawbars = [0.8, 0.3, 0.7, 0.5, 0.3, 0.4, 0.2, 0.15, 0.1]
    harmonics = [0.5, 1.5, 1, 2, 3, 4, 5, 6, 8]
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        # Slow attack, sustain, no decay
        env = min(1.0, t * 20)  # 50ms attack
        s = 0.0
        for db, h in zip(drawbars, harmonics):
            s += math.sin(2 * math.pi * freq * h * t) * db
        s /= sum(drawbars)
        # Slight Leslie tremolo
        tremolo = 1.0 + 0.05 * math.sin(2 * math.pi * 5.5 * t)
        samples.append(s * env * tremolo * 0.7)
    return samples


def generate_instrument(name: str, generator, notes: list[int]):
    """Generate an SFZ instrument with sampled notes."""
    inst_dir = os.path.join(BASE_DIR, name)
    samples_dir = os.path.join(inst_dir, "samples")
    os.makedirs(samples_dir, exist_ok=True)

    regions = []
    for idx, note in enumerate(notes):
        freq = midi_to_freq(note)
        lo = notes[idx - 1] + (note - notes[idx - 1]) // 2 + 1 if idx > 0 else 0
        hi = note + (notes[idx + 1] - note) // 2 if idx < len(notes) - 1 else 127

        wav_name = f"note_{note}.wav"
        wav_path = os.path.join(samples_dir, wav_name)
        samples = generator(freq)
        write_wav(wav_path, samples)

        regions.append(f"""<region>
sample=samples/{wav_name}
lokey={lo} hikey={hi}
pitch_keycenter={note}
""")
        print(f"  {name}: note {note} ({lo}-{hi})")

    sfz_content = f"// {name} — generated instrument\n\n<group>\nlovel=0 hivel=127\n\n"
    sfz_content += "\n".join(regions)

    sfz_path = os.path.join(inst_dir, f"{name}.sfz")
    with open(sfz_path, "w") as f:
        f.write(sfz_content)

    print(f"  -> {sfz_path}")


def main():
    # Sample every 6 semitones (covers 2+ octaves with pitch shifting)
    sample_notes = [36, 42, 48, 54, 60, 66, 72, 78, 84]

    print("Generating built-in instruments...\n")

    print("[1/3] Sine Piano")
    generate_instrument("sine-piano", generate_piano_tone, sample_notes)

    print("\n[2/3] Electric Piano")
    generate_instrument("electric-piano", generate_epiano_tone, sample_notes)

    print("\n[3/3] Organ")
    generate_instrument("organ", generate_organ_tone, sample_notes)

    print("\nDone! 3 instruments ready in instruments/")


if __name__ == "__main__":
    main()
