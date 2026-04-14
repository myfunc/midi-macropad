import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

/* ── Constants ── */

// 2 octaves: C3 (MIDI 48) through B4 (MIDI 71) = 24 notes
const OCTAVE_START_A = 48 // C3
const OCTAVE_START_B = 60 // C4
const KEYS_PER_BANK = 24  // 2 octaves per bank actually, but range is 24 semitones

// White key indices within an octave (0-based from C)
const WHITE_OFFSETS = [0, 2, 4, 5, 7, 9, 11]
// Black key indices within an octave
const BLACK_OFFSETS = [1, 3, 6, 8, 10]

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

interface KeyInfo {
  note: number    // MIDI note number
  name: string    // e.g. "C3"
  isBlack: boolean
  octave: number
  posIndex: number // position index among white keys for layout
}

/** Build key layout for a 2-octave range starting at `startNote`. */
function buildKeys(startNote: number): KeyInfo[] {
  const keys: KeyInfo[] = []
  const startOctave = Math.floor(startNote / 12) - 1 // MIDI octave convention

  for (let i = 0; i < KEYS_PER_BANK; i++) {
    const note = startNote + i
    const noteInOctave = note % 12
    const octave = Math.floor(note / 12) - 1
    const isBlack = BLACK_OFFSETS.includes(noteInOctave)
    const name = `${NOTE_NAMES[noteInOctave]}${octave}`

    // posIndex: count white keys before this note in the range
    let whiteCount = 0
    for (let j = 0; j < i; j++) {
      const n = (startNote + j) % 12
      if (!BLACK_OFFSETS.includes(n)) whiteCount++
    }

    keys.push({ note, name, isBlack, octave, posIndex: whiteCount })
  }

  return keys
}

/** Count total white keys in a range. */
function countWhiteKeys(startNote: number, count: number): number {
  let n = 0
  for (let i = 0; i < count; i++) {
    if (!BLACK_OFFSETS.includes((startNote + i) % 12)) n++
  }
  return n
}

/* ── Velocity calculation ── */

function velocityFromY(y: number, keyHeight: number): number {
  // Top = soft (~40), bottom = hard (~127)
  const ratio = Math.max(0, Math.min(1, y / keyHeight))
  return Math.round(40 + ratio * 87)
}

/* ── API calls ── */

function sendNoteOn(note: number, velocity: number, bank: 'A' | 'B') {
  fetch('/api/piano/note', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note, velocity, bank }),
  }).catch(e => console.error('[PianoPanel] note on failed:', e))
}

function sendNoteOff(note: number) {
  fetch('/api/piano/note/off', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  }).catch(e => console.error('[PianoPanel] note off failed:', e))
}

/* ── Instrument type ── */

interface InstrumentInfo {
  name: string
}

/* ── FX state type ── */

interface FxState {
  [fxName: string]: { [param: string]: number }
}

/* ── Styles (inline, matching dark theme) ── */

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column' as const,
    height: '100%',
    padding: '8px 12px',
    gap: 8,
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexShrink: 0,
    flexWrap: 'wrap' as const,
  },
  bankBtn: (active: boolean) => ({
    padding: '2px 8px',
    fontSize: 12,
    cursor: 'pointer',
    border: 'none',
    borderRadius: 3,
    background: active ? '#4a6fa5' : '#2a2a3a',
    color: active ? '#fff' : '#888',
  }),
  instrumentSelect: {
    background: 'var(--bg-tertiary)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--chip-radius)',
    padding: '3px 8px',
    color: 'var(--text-primary)',
    fontSize: 12,
    outline: 'none',
    minWidth: 120,
    marginLeft: 'auto',
  },
  keyboardWrapper: {
    flex: 1,
    position: 'relative' as const,
    minHeight: 80,
    userSelect: 'none' as const,
    touchAction: 'none' as const,
  },
  whiteKey: (pressed: boolean) => ({
    background: pressed ? 'var(--accent)' : '#c9d1d9',
    border: '1px solid #555',
    borderRadius: '0 0 4px 4px',
    cursor: 'pointer',
    position: 'absolute' as const,
    top: 0,
    transition: 'background 100ms ease, box-shadow 100ms ease',
    boxShadow: pressed ? '0 0 10px rgba(110, 180, 255, 0.5)' : '0 2px 3px rgba(0,0,0,0.2)',
    zIndex: 1,
  }),
  blackKey: (pressed: boolean) => ({
    background: pressed ? 'var(--accent)' : '#1c2333',
    border: '1px solid #111',
    borderRadius: '0 0 3px 3px',
    cursor: 'pointer',
    position: 'absolute' as const,
    top: 0,
    transition: 'background 100ms ease, box-shadow 100ms ease',
    boxShadow: pressed
      ? '0 0 10px rgba(110, 180, 255, 0.6)'
      : '0 2px 4px rgba(0,0,0,0.4)',
    zIndex: 2,
  }),
  keyLabel: (isBlack: boolean, pressed: boolean) => ({
    position: 'absolute' as const,
    bottom: 4,
    left: '50%',
    transform: 'translateX(-50%)',
    fontSize: 9,
    fontWeight: 500,
    pointerEvents: 'none' as const,
    color: isBlack
      ? (pressed ? '#1c2333' : '#888')
      : (pressed ? '#1c2333' : '#555'),
    whiteSpace: 'nowrap' as const,
  }),
  fxRow: {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap' as const,
    flexShrink: 0,
    padding: '4px 0',
  },
  fxBadge: {
    fontSize: 10,
    padding: '2px 6px',
    borderRadius: 3,
    background: 'var(--bg-tertiary)',
    color: 'var(--text-secondary)',
  },
} as const

/* ── PianoPanel ── */

export function PianoPanel(_props: IDockviewPanelProps) {
  const [bank, setBank] = useState<'A' | 'B'>('A')
  const [instruments, setInstruments] = useState<InstrumentInfo[]>([])
  const [currentInstrument, setCurrentInstrument] = useState('')
  const [fxState, setFxState] = useState<FxState>({})

  const pianoKeysPressed = useAppStore(s => s.pianoKeysPressed)

  const keyboardRef = useRef<HTMLDivElement>(null)
  const activeNotesRef = useRef<Set<number>>(new Set())

  // Compute keys for current bank
  const startNote = bank === 'A' ? OCTAVE_START_A : OCTAVE_START_B
  const keys = buildKeys(startNote)
  const totalWhite = countWhiteKeys(startNote, KEYS_PER_BANK)

  // Fetch instruments on mount
  useEffect(() => {
    fetch('/api/piano/instruments')
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then((data: { instruments: string[]; current: string }) => {
        setInstruments(data.instruments.map(name => ({ name })))
        setCurrentInstrument(data.current || '')
      })
      .catch(e => console.error('[PianoPanel] Failed to fetch instruments:', e))
  }, [])

  // Periodically poll FX state (lightweight, every 2s)
  useEffect(() => {
    let cancelled = false
    function fetchFx() {
      fetch('/api/piano/fx')
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
        .then((data: FxState) => { if (!cancelled) setFxState(data) })
        .catch(() => {}) // silent — FX endpoint may not exist yet
    }
    fetchFx()
    const interval = setInterval(fetchFx, 2000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  // Switch instrument
  const handleInstrumentChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    const name = e.target.value
    setCurrentInstrument(name)
    fetch('/api/piano/instrument', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).catch(err => console.error('[PianoPanel] Failed to switch instrument:', err))
  }, [])

  // Note on handler (mouse/touch)
  const handleNoteOn = useCallback((note: number, e: React.MouseEvent | React.TouchEvent) => {
    const target = e.currentTarget as HTMLElement
    const rect = target.getBoundingClientRect()

    let clientY: number
    if ('touches' in e) {
      clientY = e.touches[0].clientY
    } else {
      clientY = (e as React.MouseEvent).clientY
    }

    const relativeY = clientY - rect.top
    const velocity = velocityFromY(relativeY, rect.height)

    if (!activeNotesRef.current.has(note)) {
      activeNotesRef.current.add(note)
      sendNoteOn(note, velocity, bank)
    }
  }, [bank])

  // Note off handler
  const handleNoteOff = useCallback((note: number) => {
    if (activeNotesRef.current.has(note)) {
      activeNotesRef.current.delete(note)
      sendNoteOff(note)
    }
  }, [])

  // Global mouseup/touchend to release all notes
  useEffect(() => {
    const releaseAll = () => {
      activeNotesRef.current.forEach(note => sendNoteOff(note))
      activeNotesRef.current.clear()
    }
    window.addEventListener('mouseup', releaseAll)
    window.addEventListener('touchend', releaseAll)
    return () => {
      window.removeEventListener('mouseup', releaseAll)
      window.removeEventListener('touchend', releaseAll)
    }
  }, [])

  // Compute FX display entries
  const fxEntries = Object.entries(fxState).flatMap(([fxName, params]) =>
    Object.entries(params).map(([param, value]) => ({
      label: `${fxName}.${param}`,
      value: typeof value === 'number' ? value.toFixed(2) : String(value),
    }))
  )

  return (
    <div style={styles.container}>
      {/* Header: Bank toggle + Instrument selector */}
      <div style={styles.header}>
        <div style={{ display: 'flex', gap: 2 }}>
          <button style={styles.bankBtn(bank === 'A')} onClick={() => setBank('A')}>(A)</button>
          <button style={styles.bankBtn(bank === 'B')} onClick={() => setBank('B')}>(B)</button>
        </div>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {bank === 'A' ? 'C3-B4' : 'C4-B5'}
        </span>
        <select
          style={styles.instrumentSelect}
          value={currentInstrument}
          onChange={handleInstrumentChange}
        >
          {instruments.length === 0 && (
            <option value="">No instruments</option>
          )}
          {instruments.map(inst => (
            <option key={inst.name} value={inst.name}>{inst.name}</option>
          ))}
        </select>
      </div>

      {/* Keyboard */}
      <div ref={keyboardRef} style={styles.keyboardWrapper}>
        <KeyboardLayout
          keys={keys}
          totalWhite={totalWhite}
          pressedNotes={pianoKeysPressed}
          onNoteOn={handleNoteOn}
          onNoteOff={handleNoteOff}
        />
      </div>

      {/* FX indicators */}
      {fxEntries.length > 0 && (
        <div style={styles.fxRow}>
          {fxEntries.map(fx => (
            <span key={fx.label} style={styles.fxBadge}>
              {fx.label}: {fx.value}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Keyboard Layout (responsive) ── */

interface KeyboardLayoutProps {
  keys: KeyInfo[]
  totalWhite: number
  pressedNotes: Set<number>
  onNoteOn: (note: number, e: React.MouseEvent | React.TouchEvent) => void
  onNoteOff: (note: number) => void
}

function KeyboardLayout({ keys, totalWhite, pressedNotes, onNoteOn, onNoteOff }: KeyboardLayoutProps) {
  const whiteKeys = keys.filter(k => !k.isBlack)
  const blackKeys = keys.filter(k => k.isBlack)

  // Percentage-based sizing for responsiveness
  const whiteWidthPct = 100 / totalWhite
  const blackWidthPct = whiteWidthPct * 0.6
  const blackHeightPct = 60 // % of container height

  // Build a map: for each white key, track its left position index
  // whiteKeys are already in order, posIndex 0..totalWhite-1
  const whitePositions = new Map<number, number>()
  let wIdx = 0
  for (const k of keys) {
    if (!k.isBlack) {
      whitePositions.set(k.note, wIdx)
      wIdx++
    }
  }

  // For black keys: position between the two adjacent white keys
  function blackKeyLeft(key: KeyInfo): number {
    // Black key sits to the right of its preceding white key
    // Find the white key index just before this black key
    const prevWhiteIdx = key.posIndex // posIndex = count of white keys before this note
    return prevWhiteIdx * whiteWidthPct - blackWidthPct / 2
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* White keys */}
      {whiteKeys.map((key, i) => {
        const pressed = pressedNotes.has(key.note)
        return (
          <div
            key={key.note}
            style={{
              ...styles.whiteKey(pressed),
              left: `${i * whiteWidthPct}%`,
              width: `${whiteWidthPct}%`,
              height: '100%',
            }}
            onMouseDown={e => { e.preventDefault(); onNoteOn(key.note, e) }}
            onMouseUp={() => onNoteOff(key.note)}
            onMouseLeave={() => onNoteOff(key.note)}
            onTouchStart={e => { e.preventDefault(); onNoteOn(key.note, e) }}
            onTouchEnd={() => onNoteOff(key.note)}
          >
            <span style={styles.keyLabel(false, pressed)}>
              {key.name}
            </span>
          </div>
        )
      })}

      {/* Black keys */}
      {blackKeys.map(key => {
        const pressed = pressedNotes.has(key.note)
        const left = blackKeyLeft(key)
        return (
          <div
            key={key.note}
            style={{
              ...styles.blackKey(pressed),
              left: `${left}%`,
              width: `${blackWidthPct}%`,
              height: `${blackHeightPct}%`,
            }}
            onMouseDown={e => { e.preventDefault(); onNoteOn(key.note, e) }}
            onMouseUp={() => onNoteOff(key.note)}
            onMouseLeave={() => onNoteOff(key.note)}
            onTouchStart={e => { e.preventDefault(); onNoteOn(key.note, e) }}
            onTouchEnd={() => onNoteOff(key.note)}
          >
            <span style={styles.keyLabel(true, pressed)}>
              {key.name.replace('#', '#')}
            </span>
          </div>
        )
      })}
    </div>
  )
}
