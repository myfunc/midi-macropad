# Instruments Directory

Place SFZ or SF2 instrument files here for the Piano plugin.

## Supported Formats

### SFZ (recommended)

A directory containing a `.sfz` text file and WAV samples:

```
instruments/
  my-piano/
    my-piano.sfz
    samples/
      c3.wav
      c4.wav
      c5.wav
```

SFZ format uses text opcodes to map WAV files to key/velocity ranges:

```sfz
<group>
lovel=0 hivel=127

<region>
sample=samples/c3.wav
lokey=36 hikey=47
pitch_keycenter=48

<region>
sample=samples/c4.wav
lokey=48 hikey=59
pitch_keycenter=60
key=60

<region>
sample=samples/c5.wav
lokey=60 hikey=72
pitch_keycenter=72
```

Supported opcodes: `sample`, `lokey`, `hikey`, `key`, `pitch_keycenter`,
`lovel`, `hivel`, `volume` (dB), `tune` (cents), `offset`, `loop_mode`,
`loop_start`, `loop_end`. Note names like `c4`, `f#3` are accepted for key values.

### SF2 (SoundFont 2)

A single `.sf2` file placed directly in this directory:

```
instruments/
  grand-piano.sf2
```

Requires the `sf2utils` Python package (`pip install sf2utils`).
The first preset in the SF2 file is loaded by default.

## Notes

- WAV files should be 16-bit or 24-bit, mono or stereo (converted to mono on load).
- Sample rates are automatically converted to 44100 Hz.
- Notes outside the pad range (16-31) are routed to the Piano plugin.
- The plugin pitch-shifts samples to cover notes beyond their defined key range.
