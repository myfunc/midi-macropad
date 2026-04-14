import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { PanelHeader } from '../components/PanelHeader'
import type { PianoKeyMapping, Panel } from '../types'
import type { IDockviewPanelProps } from 'dockview-react'

/* ── Constants ── */

// Piano map bank accepts notes 36..72; play bank currently renders 2 octaves
// starting at 48 (C3) or 60 (C4) — kept for legacy play panels, which use
// bank='play' (single range, selectable via internal state if needed later).
const PLAY_OCTAVE_START = 48 // C3
const PLAY_KEYS = 24

const MAP_RANGE_START = 36 // C2
const MAP_RANGE_END = 72   // C5 (inclusive)

// Black/white layout constants
const BLACK_OFFSETS = [1, 3, 6, 8, 10]
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

interface KeyInfo {
  note: number
  name: string
  isBlack: boolean
  octave: number
}

function buildKeys(startNote: number, count: number): KeyInfo[] {
  const keys: KeyInfo[] = []
  for (let i = 0; i < count; i++) {
    const note = startNote + i
    const noteInOctave = note % 12
    const octave = Math.floor(note / 12) - 1
    const isBlack = BLACK_OFFSETS.includes(noteInOctave)
    const name = `${NOTE_NAMES[noteInOctave]}${octave}`
    keys.push({ note, name, isBlack, octave })
  }
  return keys
}

function countWhiteKeys(startNote: number, count: number): number {
  let n = 0
  for (let i = 0; i < count; i++) {
    if (!BLACK_OFFSETS.includes((startNote + i) % 12)) n++
  }
  return n
}

function velocityFromY(y: number, keyHeight: number): number {
  const ratio = Math.max(0, Math.min(1, y / keyHeight))
  return Math.round(40 + ratio * 87)
}

/* ── API helpers ── */

function sendNoteOn(note: number, velocity: number, bank: 'play' | 'map') {
  fetch('/api/piano/note', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note, velocity, bank }),
  }).catch(e => console.error('[PianoPanel] note on failed:', e))
}

function sendNoteOff(note: number, bank: 'play' | 'map') {
  fetch('/api/piano/note/off', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note, bank }),
  }).catch(e => console.error('[PianoPanel] note off failed:', e))
}

/* ── Styles ── */

const styles = {
  container: {
    display: 'flex', flexDirection: 'column' as const,
    height: '100%', padding: '4px 8px', gap: 8, overflow: 'hidden',
  },
  subheader: {
    display: 'flex', alignItems: 'center', gap: 8,
    fontSize: 11, color: 'var(--text-muted)',
    flexShrink: 0, flexWrap: 'wrap' as const,
  },
  instrumentSelect: {
    background: 'var(--bg-tertiary)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--chip-radius)',
    padding: '3px 8px', color: 'var(--text-primary)',
    fontSize: 12, outline: 'none', minWidth: 120,
    marginLeft: 'auto',
  },
  keyboardWrapper: {
    flex: 1, position: 'relative' as const, minHeight: 80,
    userSelect: 'none' as const, touchAction: 'none' as const,
  },
  whiteKey: (pressed: boolean, mapped: boolean, selected: boolean) => ({
    background: pressed
      ? 'var(--accent)'
      : (selected ? '#d4e6ff' : (mapped ? '#a7c7ef' : '#c9d1d9')),
    border: selected ? '2px solid var(--accent)' : '1px solid #555',
    borderRadius: '0 0 4px 4px',
    cursor: 'pointer',
    position: 'absolute' as const, top: 0,
    transition: 'background 100ms ease, box-shadow 100ms ease',
    boxShadow: pressed ? '0 0 10px rgba(110, 180, 255, 0.5)' : '0 2px 3px rgba(0,0,0,0.2)',
    zIndex: 1,
  }),
  blackKey: (pressed: boolean, mapped: boolean, selected: boolean) => ({
    background: pressed
      ? 'var(--accent)'
      : (selected ? '#4a6fa5' : (mapped ? '#2d4a70' : '#1c2333')),
    border: selected ? '2px solid var(--accent)' : '1px solid #111',
    borderRadius: '0 0 3px 3px',
    cursor: 'pointer',
    position: 'absolute' as const, top: 0,
    transition: 'background 100ms ease, box-shadow 100ms ease',
    boxShadow: pressed
      ? '0 0 10px rgba(110, 180, 255, 0.6)'
      : '0 2px 4px rgba(0,0,0,0.4)',
    zIndex: 2,
  }),
  keyLabel: (isBlack: boolean, pressed: boolean) => ({
    position: 'absolute' as const, bottom: 4, left: '50%',
    transform: 'translateX(-50%)',
    fontSize: 9, fontWeight: 500, pointerEvents: 'none' as const,
    color: isBlack ? (pressed ? '#1c2333' : '#ccc') : (pressed ? '#1c2333' : '#333'),
    whiteSpace: 'nowrap' as const,
  }),
  mapLabel: (isBlack: boolean) => ({
    position: 'absolute' as const, top: 4, left: '50%',
    transform: 'translateX(-50%)',
    fontSize: 9, fontWeight: 600, pointerEvents: 'none' as const,
    color: isBlack ? '#e0e8ff' : '#1a2a4a',
    whiteSpace: 'nowrap' as const,
    maxWidth: '90%',
    overflow: 'hidden' as const,
    textOverflow: 'ellipsis' as const,
  }),
} as const

/* ── PianoPanel ── */

interface InstrumentInfo { name: string }

/**
 * Top-level wrapper that guards against undefined panel state.
 *
 * Hook-count stability: we must NOT call any hooks after an early return,
 * so any component that relies on ``panel`` being truthy (and thus calls
 * ``useEffect``/``useMemo``/``useCallback``) lives in ``PianoPanelInner``,
 * which is only mounted once ``panel`` has been hydrated by the store.
 */
export function PianoPanel(props: IDockviewPanelProps) {
  const instanceId = props.api.id
  const panel = useAppStore(s => s.panels[instanceId])

  if (!panel) {
    return (
      <div style={styles.container}>
        <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          Loading panel…
        </div>
      </div>
    )
  }

  return <PianoPanelInner instanceId={instanceId} panel={panel} />
}

interface PianoPanelInnerProps {
  instanceId: string
  panel: Panel
}

function PianoPanelInner({ instanceId, panel }: PianoPanelInnerProps) {
  const pianoPresets = useAppStore(s => s.pianoPresets)
  const selectedPianoNote = useAppStore(s => s.selectedPianoNote)
  const selectPianoNote = useAppStore(s => s.selectPianoNote)
  const pianoKeysPressed = useAppStore(s => s.pianoKeysPressed)
  const fetchPianoPresets = useAppStore(s => s.fetchPianoPresets)

  const [instruments, setInstruments] = useState<InstrumentInfo[]>([])
  const [currentInstrument, setCurrentInstrument] = useState('')
  const [playOffset, setPlayOffset] = useState(0) // octave shift for play bank

  const activeNotesRef = useRef<Set<number>>(new Set())

  const bank: 'play' | 'map' = panel.bank === 'map' ? 'map' : 'play'

  // Fetch piano presets once on mount.
  useEffect(() => {
    if (pianoPresets.length === 0) fetchPianoPresets()
  }, [pianoPresets.length, fetchPianoPresets])

  // Fetch instruments (play bank only).
  useEffect(() => {
    if (bank !== 'play') return
    fetch('/api/piano/instruments')
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then((data: { instruments: string[]; current: string }) => {
        setInstruments(data.instruments.map(name => ({ name })))
        setCurrentInstrument(data.current || '')
      })
      .catch(() => {})
  }, [bank])

  const handleInstrumentChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    const name = e.target.value
    setCurrentInstrument(name)
    fetch('/api/piano/instrument', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).catch(() => {})
  }, [])

  // Compute keyboard range for the active bank.
  const { startNote, keyCount } = useMemo(() => {
    if (bank === 'map') {
      return {
        startNote: MAP_RANGE_START,
        keyCount: MAP_RANGE_END - MAP_RANGE_START + 1,
      }
    }
    return {
      startNote: PLAY_OCTAVE_START + playOffset * 12,
      keyCount: PLAY_KEYS,
    }
  }, [bank, playOffset])

  const keys = useMemo(() => buildKeys(startNote, keyCount), [startNote, keyCount])
  const totalWhite = countWhiteKeys(startNote, keyCount)

  // Mapping lookup for the current preset (map bank).
  const mapping: Map<number, PianoKeyMapping> = useMemo(() => {
    const m = new Map<number, PianoKeyMapping>()
    if (bank !== 'map') return m
    const preset = pianoPresets.find(p => p.name === panel.preset)
    if (!preset) return m
    for (const k of preset.keys) m.set(k.note, k)
    return m
  }, [bank, pianoPresets, panel.preset])

  // Press / release handlers
  const handleNoteOn = useCallback((note: number, e: React.MouseEvent | React.TouchEvent) => {
    if (bank === 'map') {
      // Select the key for editing in Properties; also dispatch for action.
      selectPianoNote(note)
      sendNoteOn(note, 100, 'map')
      return
    }
    const target = e.currentTarget as HTMLElement
    const rect = target.getBoundingClientRect()
    const clientY = 'touches' in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY
    const relativeY = clientY - rect.top
    const velocity = velocityFromY(relativeY, rect.height)
    if (!activeNotesRef.current.has(note)) {
      activeNotesRef.current.add(note)
      sendNoteOn(note, velocity, 'play')
    }
  }, [bank, selectPianoNote])

  const handleNoteOff = useCallback((note: number) => {
    if (bank === 'map') return  // Map bank has no hold semantics for mouse.
    if (activeNotesRef.current.has(note)) {
      activeNotesRef.current.delete(note)
      sendNoteOff(note, 'play')
    }
  }, [bank])

  // Global release — only meaningful for play bank.
  useEffect(() => {
    if (bank !== 'play') return
    const releaseAll = () => {
      activeNotesRef.current.forEach(note => sendNoteOff(note, 'play'))
      activeNotesRef.current.clear()
    }
    window.addEventListener('mouseup', releaseAll)
    window.addEventListener('touchend', releaseAll)
    return () => {
      window.removeEventListener('mouseup', releaseAll)
      window.removeEventListener('touchend', releaseAll)
    }
  }, [bank])

  return (
    <div style={styles.container}>
      <PanelHeader instanceId={instanceId} panel={panel} />
      <div style={styles.subheader}>
        {bank === 'play' ? (
          <>
            <span>Range: {keys[0]?.name} – {keys[keys.length - 1]?.name}</span>
            <button
              onClick={() => setPlayOffset(o => Math.max(-2, o - 1))}
              style={{ fontSize: 10, padding: '2px 6px' }}
              title="Octave down"
            >Oct −</button>
            <button
              onClick={() => setPlayOffset(o => Math.min(2, o + 1))}
              style={{ fontSize: 10, padding: '2px 6px' }}
              title="Octave up"
            >Oct +</button>
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
          </>
        ) : (
          <>
            <span>Map mode — click a key to edit its action in Properties</span>
            <span style={{ marginLeft: 'auto' }}>
              Range: {MAP_RANGE_START}–{MAP_RANGE_END}
            </span>
          </>
        )}
      </div>
      <div style={styles.keyboardWrapper}>
        <KeyboardLayout
          keys={keys}
          totalWhite={totalWhite}
          startNote={startNote}
          pressedNotes={pianoKeysPressed}
          selectedNote={bank === 'map' ? selectedPianoNote : null}
          mapping={mapping}
          mapMode={bank === 'map'}
          onNoteOn={handleNoteOn}
          onNoteOff={handleNoteOff}
        />
      </div>
    </div>
  )
}

/* ── Keyboard ── */

interface KeyboardLayoutProps {
  keys: KeyInfo[]
  totalWhite: number
  startNote: number
  pressedNotes: Set<number>
  selectedNote: number | null
  mapping: Map<number, PianoKeyMapping>
  mapMode: boolean
  onNoteOn: (note: number, e: React.MouseEvent | React.TouchEvent) => void
  onNoteOff: (note: number) => void
}

function KeyboardLayout({
  keys, totalWhite, startNote,
  pressedNotes, selectedNote, mapping, mapMode,
  onNoteOn, onNoteOff,
}: KeyboardLayoutProps) {
  const whiteKeys = keys.filter(k => !k.isBlack)
  const blackKeys = keys.filter(k => k.isBlack)

  const whiteWidthPct = 100 / totalWhite
  const blackWidthPct = whiteWidthPct * 0.6
  const blackHeightPct = 60

  // For black keys we need the index-of-white-keys-before-this-black-key,
  // computed relative to the given startNote.
  function blackKeyLeft(key: KeyInfo): number {
    let whitesBefore = 0
    for (let n = startNote; n < key.note; n++) {
      if (!BLACK_OFFSETS.includes(n % 12)) whitesBefore++
    }
    return whitesBefore * whiteWidthPct - blackWidthPct / 2
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {whiteKeys.map((key, i) => {
        const pressed = pressedNotes.has(key.note)
        const mapped = mapMode && mapping.has(key.note) && !!mapping.get(key.note)?.action
        const selected = mapMode && selectedNote === key.note
        const mapLabel = mapMode ? mapping.get(key.note)?.label : undefined
        return (
          <div
            key={key.note}
            style={{
              ...styles.whiteKey(pressed, mapped, selected),
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
            {mapLabel && (
              <span style={styles.mapLabel(false)}>{mapLabel}</span>
            )}
            <span style={styles.keyLabel(false, pressed)}>{key.name}</span>
          </div>
        )
      })}
      {blackKeys.map(key => {
        const pressed = pressedNotes.has(key.note)
        const mapped = mapMode && mapping.has(key.note) && !!mapping.get(key.note)?.action
        const selected = mapMode && selectedNote === key.note
        const mapLabel = mapMode ? mapping.get(key.note)?.label : undefined
        const left = blackKeyLeft(key)
        return (
          <div
            key={key.note}
            style={{
              ...styles.blackKey(pressed, mapped, selected),
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
            {mapLabel && (
              <span style={styles.mapLabel(true)}>{mapLabel}</span>
            )}
            <span style={styles.keyLabel(true, pressed)}>{key.name}</span>
          </div>
        )
      })}
    </div>
  )
}
