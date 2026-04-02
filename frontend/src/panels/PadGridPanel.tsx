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

function getIndicatorColor(entry: PadEntry): string {
  if (!entry.action_type) return 'transparent'
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

  return (
    <div
      className={`pad ${isSelected ? 'selected' : ''} ${isFlashed ? 'flashed' : ''} ${isEmpty ? 'empty' : ''}`}
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
      <span className="pad-label">{label}</span>
      <span className="pad-indicator" style={{ background: entry ? getIndicatorColor(entry) : 'transparent' }} />
    </div>
  )
}

function Knob({ cc, label, value }: { cc: number; label: string; value: number }) {
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

export function PadGridPanel(_props: IDockviewPanelProps) {
  const pads = useAppStore(s => s.pads)
  const knobs = useAppStore(s => s.knobs)
  const selectedNote = useAppStore(s => s.selectedNote)
  const flashedPads = useAppStore(s => s.flashedPads)
  const selectPad = useAppStore(s => s.selectPad)

  // MPK Mini Play layout: top row = 20-23, bottom row = 16-19
  const bankA = [20, 21, 22, 23, 16, 17, 18, 19]
  const bankB = [28, 29, 30, 31, 24, 25, 26, 27]

  function triggerPad(note: number) {
    fetch(`/api/pads/${note}/press`, { method: 'POST' })
  }

  return (
    <div className="pad-area">
      <div className="bank-row">
        <div className="bank">
          <div className="bank-header">Bank A (16-23)</div>
          <div className="pad-grid">
            {bankA.map(n => (
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
        <div className="bank">
          <div className="bank-header">Bank B (24-31)</div>
          <div className="pad-grid">
            {bankB.map(n => (
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
        <div className="knobs-section">
          <div className="bank-header">Knobs</div>
          <div className="knob-grid">
            {knobs.map(k => (
              <Knob key={k.cc} cc={k.cc} label={k.label} value={k.value} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
