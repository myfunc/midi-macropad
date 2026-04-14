import { useCallback, useMemo, useState } from 'react'
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
import { PanelHeader } from '../components/PanelHeader'
import type { PadEntry, PanelBank } from '../types'
import type { IDockviewPanelProps } from 'dockview-react'

/* Constants */
const BANK_A_NOTES = [20, 21, 22, 23, 16, 17, 18, 19]
const BANK_B_NOTES = [28, 29, 30, 31, 24, 25, 26, 27]

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

interface PadViewProps {
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
}: PadViewProps) {
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

function SortablePad({ note, entry, isSelected, isFlashed, onSelect, onTrigger }: {
  note: number
  entry: PadEntry | undefined
  isSelected: boolean
  isFlashed: boolean
  onSelect: () => void
  onTrigger: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: String(note) })

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

function triggerPad(note: number) {
  fetch(`/api/pads/${note}/press`, { method: 'POST' })
}

export function PadPanel(props: IDockviewPanelProps) {
  const instanceId = props.api.id
  const panel = useAppStore(s => s.panels[instanceId])
  const pads = useAppStore(s => s.pads)
  const selectedPadKey = useAppStore(s => s.selectedPadKey)
  const flashedPads = useAppStore(s => s.flashedPads)
  const selectPad = useAppStore(s => s.selectPad)

  const bank: PanelBank = (panel?.bank === 'B' ? 'B' : 'A')
  const presetName = panel?.preset ?? ''
  const notes = bank === 'A' ? BANK_A_NOTES : BANK_B_NOTES

  const sortableIds = useMemo(() => notes.map(n => String(n)), [notes])
  const [activeId, setActiveId] = useState<string | null>(null)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string)
  }

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = event
    if (!over || active.id === over.id) return

    const noteA = parseInt(active.id as string, 10)
    const noteB = parseInt(over.id as string, 10)
    const preset = presetName
    const keyA = `${preset}:${noteA}`
    const keyB = `${preset}:${noteB}`

    const currentPads = useAppStore.getState().pads
    const padA = currentPads[keyA]
    const padB = currentPads[keyB]
    if (padA && padB) {
      useAppStore.getState().updatePads({
        ...currentPads,
        [keyA]: { ...padB, note: noteA },
        [keyB]: { ...padA, note: noteB },
      })
    }

    fetch('/api/pads/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset, note_a: noteA, note_b: noteB }),
    }).catch((e) => console.error(`[PadPanel] swap failed:`, e))
  }, [presetName])

  const activeNote = activeId ? parseInt(activeId, 10) : 0
  const activeEntry = activeId != null ? pads[`${presetName}:${activeNote}`] : undefined

  if (!panel) {
    return <div className="pad-area" style={{ padding: 16, color: '#888' }}>
      Panel not registered.
    </div>
  }

  return (
    <div className="pad-area">
      <PanelHeader instanceId={instanceId} panel={panel} />
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={sortableIds} strategy={rectSortingStrategy}>
          <div className="pad-grid responsive">
            {notes.map(n => {
              const compositeKey = `${presetName}:${n}`
              return (
                <SortablePad
                  key={n}
                  note={n}
                  entry={pads[compositeKey]}
                  isSelected={selectedPadKey === compositeKey}
                  isFlashed={flashedPads.has(compositeKey)}
                  onSelect={() => selectPad(compositeKey)}
                  onTrigger={() => triggerPad(n)}
                />
              )
            })}
          </div>
        </SortableContext>
        <DragOverlay>
          {activeId != null && (
            <PadView
              note={activeNote}
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
