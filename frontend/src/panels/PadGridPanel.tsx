import { useCallback, useState } from 'react'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  rectSortingStrategy,
  useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useAppStore } from '../stores/useAppStore'
import { PanelPresetSwitcher } from '../components/PanelPresetSwitcher'
import type { PadEntry, KnobEntry } from '../types'
import type { IDockviewPanelProps } from 'dockview-react'

/* ── Color helpers ── */

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

/* ── Pad (presentational, used both standalone and inside SortableItem) ── */

interface PadProps {
  note: number
  entry: PadEntry | undefined
  isSelected: boolean
  isFlashed: boolean
  onSelect: () => void
  onTrigger: () => void
  isDragging?: boolean
  style?: React.CSSProperties
  setNodeRef?: (el: HTMLElement | null) => void
  attributes?: Record<string, unknown>
  listeners?: Record<string, unknown>
}

function PadView({
  note, entry, isSelected, isFlashed, onSelect, onTrigger,
  isDragging, style, setNodeRef, attributes, listeners,
}: PadProps) {
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
    isDragging && 'dragging',
  ].filter(Boolean).join(' ')

  return (
    <div
      ref={setNodeRef}
      className={classes}
      style={{
        ...(hasAccent ? { borderBottomColor: accent, borderBottomWidth: 3 } : {}),
        ...style,
      }}
      onClick={onSelect}
      onDoubleClick={(e) => { e.stopPropagation(); onTrigger() }}
      {...attributes}
      {...listeners}
    >
      <span className="pad-number">{note}</span>
      {isLocked && <span className="lock-icon">&#128274;</span>}
      {isToggle && <span className={`pad-state ${isOn ? 'on' : 'off'}`}>{isOn ? 'ON' : 'OFF'}</span>}
      <span className="pad-label">{label}</span>
      {hasAccent && <span className="pad-indicator" style={{ background: accent }} />}
    </div>
  )
}

/* ── SortablePad wrapper ── */

function SortablePad({ note, entry, isSelected, isFlashed, onSelect, onTrigger }: {
  note: number
  entry: PadEntry | undefined
  isSelected: boolean
  isFlashed: boolean
  onSelect: () => void
  onTrigger: () => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: note })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : undefined,
    zIndex: isDragging ? 10 : undefined,
  }

  return (
    <PadView
      note={note} entry={entry}
      isSelected={isSelected} isFlashed={isFlashed}
      onSelect={onSelect} onTrigger={onTrigger}
      isDragging={isDragging}
      style={style}
      setNodeRef={setNodeRef}
      attributes={attributes as unknown as Record<string, unknown>}
      listeners={listeners as unknown as Record<string, unknown>}
    />
  )
}

/* ── triggerPad ── */

function triggerPad(note: number) {
  fetch(`/api/pads/${note}/press`, { method: 'POST' })
}

/* ── BankPanel with DnD + responsive grid + preset switcher ── */

function BankPanel({ notes, panelId }: { notes: number[]; panelId: string }) {
  const pads = useAppStore(s => s.pads)
  const selectedNote = useAppStore(s => s.selectedNote)
  const flashedPads = useAppStore(s => s.flashedPads)
  const selectPad = useAppStore(s => s.selectPad)
  const panelOrder = useAppStore(s => s.panelPresets[panelId]?.order)
  const setPanelOrder = useAppStore(s => s.setPanelOrder)

  const [activeId, setActiveId] = useState<number | null>(null)

  // Use custom order if available, otherwise default note order
  const orderedNotes = panelOrder && panelOrder.length === notes.length
    ? panelOrder
    : notes

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as number)
  }

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = event
    if (!over || active.id === over.id) return

    // Get fresh order from store to avoid stale closure
    const currentPanelOrder = useAppStore.getState().panelPresets[panelId]?.order
    const currentOrder = currentPanelOrder && currentPanelOrder.length === notes.length
      ? currentPanelOrder
      : notes

    const oldIndex = currentOrder.indexOf(active.id as number)
    const newIndex = currentOrder.indexOf(over.id as number)
    if (oldIndex === -1 || newIndex === -1) return

    const newOrder = [...currentOrder]
    newOrder.splice(oldIndex, 1)
    newOrder.splice(newIndex, 0, active.id as number)

    const previousOrder = [...currentOrder]
    setPanelOrder(panelId, newOrder)
    fetch(`/api/panels/${panelId}/order`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: newOrder }),
    }).then(r => {
      if (!r.ok) throw new Error(r.statusText)
    }).catch((e) => {
      console.error(`[PadGrid] Failed to save order for ${panelId}:`, e)
      setPanelOrder(panelId, previousOrder)
    })
  }, [panelId, notes, setPanelOrder])

  const activeEntry = activeId != null ? pads[String(activeId)] : undefined

  return (
    <div className="pad-area">
      <PanelPresetSwitcher panelId={panelId} />
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={orderedNotes} strategy={rectSortingStrategy}>
          <div className="pad-grid responsive">
            {orderedNotes.map(n => (
              <SortablePad
                key={n} note={n}
                entry={pads[String(n)]}
                isSelected={selectedNote === n}
                isFlashed={flashedPads.has(n)}
                onSelect={() => selectPad(n)}
                onTrigger={() => triggerPad(n)}
              />
            ))}
          </div>
        </SortableContext>
        <DragOverlay>
          {activeId != null && (
            <PadView
              note={activeId}
              entry={activeEntry}
              isSelected={false}
              isFlashed={false}
              onSelect={() => {}}
              onTrigger={() => {}}
              isDragging
            />
          )}
        </DragOverlay>
      </DndContext>
    </div>
  )
}

/* ── Knob ── */

function Knob({ cc, label, value, onClick, isSelected }: { cc: number; label: string; value: number; onClick: () => void; isSelected?: boolean }) {
  const pct = Math.round((value / 127) * 100)
  const angle = (value / 127) * 270 - 135

  return (
    <div className={`knob${isSelected ? ' selected' : ''}`} onClick={onClick} title={`CC ${cc}`}>
      <div className="knob-visual">
        <div className="knob-needle" style={{ transform: `rotate(${angle}deg)` }} />
      </div>
      <div className="knob-label">{label}</div>
      <div className="knob-value">{pct}%</div>
    </div>
  )
}

/* ── SortableKnob ── */

function SortableKnob({ knob, onClick, isSelected }: { knob: KnobEntry; onClick: () => void; isSelected?: boolean }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: knob.cc })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : undefined,
    zIndex: isDragging ? 10 : undefined,
  }

  const pct = Math.round((knob.value / 127) * 100)
  const angle = (knob.value / 127) * 270 - 135

  return (
    <div
      ref={setNodeRef}
      className={`knob${isDragging ? ' dragging' : ''}${isSelected ? ' selected' : ''}`}
      style={style}
      onClick={onClick}
      title={`CC ${knob.cc}`}
      {...attributes}
      {...listeners}
    >
      <div className="knob-visual">
        <div className="knob-needle" style={{ transform: `rotate(${angle}deg)` }} />
      </div>
      <div className="knob-label">{knob.label}</div>
      <div className="knob-value">{pct}%</div>
    </div>
  )
}

/* ── Exports ── */

// MPK Mini Play layout: top row = 20-23, bottom row = 16-19
// MPK Mini Play: top row = 20-23, bottom row = 16-19
const BANK_A_NOTES = [20, 21, 22, 23, 16, 17, 18, 19]
const BANK_B_NOTES = [28, 29, 30, 31, 24, 25, 26, 27]

export function BankAPanel(_props: IDockviewPanelProps) {
  return <BankPanel notes={BANK_A_NOTES} panelId="bankA" />
}

export function BankBPanel(_props: IDockviewPanelProps) {
  return <BankPanel notes={BANK_B_NOTES} panelId="bankB" />
}

export function KnobsPanel(_props: IDockviewPanelProps) {
  const knobs = useAppStore(s => s.knobs)
  const setPanelOrder = useAppStore(s => s.setPanelOrder)
  const selectKnob = useAppStore(s => s.selectKnob)
  const selectedKnobCC = useAppStore(s => s.selectedKnobCC)
  const panelOrder = useAppStore(s => s.panelPresets['knobs']?.order)
  const [activeId, setActiveId] = useState<number | null>(null)

  const knobIds = knobs.map(k => k.cc)
  const orderedIds = panelOrder && panelOrder.length === knobIds.length
    ? panelOrder
    : knobIds

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as number)
  }

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = event
    if (!over || active.id === over.id) return

    // Get fresh order from store to avoid stale closure
    const state = useAppStore.getState()
    const currentKnobIds = state.knobs.map(k => k.cc)
    const currentPanelOrder = state.panelPresets['knobs']?.order
    const currentOrder = currentPanelOrder && currentPanelOrder.length === currentKnobIds.length
      ? currentPanelOrder
      : currentKnobIds

    const oldIndex = currentOrder.indexOf(active.id as number)
    const newIndex = currentOrder.indexOf(over.id as number)
    if (oldIndex === -1 || newIndex === -1) return

    const newOrder = [...currentOrder]
    newOrder.splice(oldIndex, 1)
    newOrder.splice(newIndex, 0, active.id as number)

    const previousOrder = [...currentOrder]
    setPanelOrder('knobs', newOrder)
    fetch('/api/panels/knobs/order', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: newOrder }),
    }).then(r => {
      if (!r.ok) throw new Error(r.statusText)
    }).catch((e) => {
      console.error('[KnobsPanel] Failed to save order:', e)
      setPanelOrder('knobs', previousOrder)
    })
  }, [setPanelOrder])

  const activeKnob = activeId != null ? knobs.find(k => k.cc === activeId) : undefined

  return (
    <div className="pad-area">
      <PanelPresetSwitcher panelId="knobs" />
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={orderedIds} strategy={rectSortingStrategy}>
          <div className="knob-grid responsive">
            {orderedIds.map(cc => {
              const k = knobs.find(kn => kn.cc === cc)
              return k ? (
                <SortableKnob
                  key={k.cc}
                  knob={k}
                  onClick={() => selectKnob(k.cc)}
                  isSelected={selectedKnobCC === k.cc}
                />
              ) : null
            })}
          </div>
        </SortableContext>
        <DragOverlay>
          {activeKnob && (
            <Knob
              cc={activeKnob.cc}
              label={activeKnob.label}
              value={activeKnob.value}
              onClick={() => {}}
            />
          )}
        </DragOverlay>
      </DndContext>
    </div>
  )
}
