import { useAppStore } from '../stores/useAppStore'
import type { PadEntry } from '../types'
import type { IDockviewPanelProps } from 'dockview-react'

const COLOR_MAP: Record<string, string> = {
  scene: '#64B4FF', mute: '#969BA5', session: '#FFC85A',
  record: '#FF7878', replay: '#B482FF', system: '#D0D0E0',
  plugin: '#5AE68C', keystroke: '#D0D0E0', app_keystroke: '#D0D0E0',
  shell: '#D0D0E0', launch: '#D0D0E0', volume: '#D0D0E0',
  scroll: '#D0D0E0',
}

function padAccentColor(entry: PadEntry | undefined): string {
  if (!entry?.action_type) return 'transparent'
  if (entry.color && (entry.color[0] !== 100 || entry.color[1] !== 100 || entry.color[2] !== 100)) {
    return `rgb(${entry.color[0]},${entry.color[1]},${entry.color[2]})`
  }
  return COLOR_MAP[entry.action_type] || '#888'
}

function Pad({ note, entry, isSelected, isFlashed, onSelect, onTrigger }: {
  note: number
  entry: PadEntry | undefined
  isSelected: boolean
  isFlashed: boolean
  onSelect: () => void
  onTrigger: () => void
}) {
  const label = entry?.label || '---'
  const isEmpty = !entry?.action_type
  const isLocked = entry?.locked
  const accent = padAccentColor(entry)
  const hasAccent = accent !== 'transparent'
  const toggleState = entry?.toggle_state
  const isToggle = toggleState !== undefined && toggleState !== null
  const isOn = toggleState === true

  const classes = [
    'pad',
    isSelected && 'selected',
    isFlashed && 'flashed',
    isEmpty && 'empty',
    isToggle && (isOn ? 'toggle-on' : 'toggle-off'),
  ].filter(Boolean).join(' ')

  return (
    <div
      className={classes}
      style={hasAccent ? { borderBottomColor: accent, borderBottomWidth: 3 } : {}}
      onClick={onSelect}
      onDoubleClick={(e) => { e.stopPropagation(); onTrigger() }}
      draggable
      onDragStart={(e) => e.dataTransfer.setData('pad-note', String(note))}
      onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('drag-over') }}
      onDragLeave={(e) => e.currentTarget.classList.remove('drag-over')}
      onDrop={(e) => {
        e.preventDefault()
        e.currentTarget.classList.remove('drag-over')
        const from = parseInt(e.dataTransfer.getData('pad-note'))
        if (from !== note) {
          fetch('/api/pads/swap', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ note_a: from, note_b: note }),
          })
        }
      }}
    >
      <span className="pad-number">{note}</span>
      {isLocked && <span className="lock-icon">&#128274;</span>}
      {isToggle && <span className={`pad-state ${isOn ? 'on' : 'off'}`}>{isOn ? 'ON' : 'OFF'}</span>}
      <span className="pad-label">{label}</span>
      {hasAccent && <span className="pad-indicator" style={{ background: accent }} />}
    </div>
  )
}

function Knob({ label, value }: { cc: number; label: string; value: number }) {
  const pct = Math.round((value / 127) * 100)
  const angle = (value / 127) * 270 - 135

  return (
    <div className="knob">
      <div className="knob-visual">
        <div className="knob-needle" style={{ transform: `rotate(${angle}deg)` }} />
      </div>
      <div className="knob-label">{label}</div>
      <div className="knob-value">{pct}%</div>
    </div>
  )
}

function triggerPad(note: number) {
  fetch(`/api/pads/${note}/press`, { method: 'POST' })
}

function BankPanel({ notes }: { notes: number[] }) {
  const pads = useAppStore(s => s.pads)
  const selectedNote = useAppStore(s => s.selectedNote)
  const flashedPads = useAppStore(s => s.flashedPads)
  const selectPad = useAppStore(s => s.selectPad)

  return (
    <div className="pad-area">
      <div className="bank">
        <div className="pad-grid">
          {notes.map(n => (
            <Pad
              key={n} note={n}
              entry={pads[String(n)]}
              isSelected={selectedNote === n}
              isFlashed={flashedPads.has(n)}
              onSelect={() => selectPad(n)}
              onTrigger={() => triggerPad(n)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// MPK Mini Play layout: top row = 20-23, bottom row = 16-19
const BANK_A_NOTES = [20, 21, 22, 23, 16, 17, 18, 19]
const BANK_B_NOTES = [28, 29, 30, 31, 24, 25, 26, 27]

export function BankAPanel(_props: IDockviewPanelProps) {
  return <BankPanel notes={BANK_A_NOTES} />
}

export function BankBPanel(_props: IDockviewPanelProps) {
  return <BankPanel notes={BANK_B_NOTES} />
}

export function KnobsPanel(_props: IDockviewPanelProps) {
  const knobs = useAppStore(s => s.knobs)
  return (
    <div className="pad-area">
      <div className="knobs-section">
        <div className="knob-grid">
          {knobs.map(k => (
            <Knob key={k.cc} cc={k.cc} label={k.label} value={k.value} />
          ))}
        </div>
      </div>
    </div>
  )
}
