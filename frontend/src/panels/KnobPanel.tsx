import { useCallback, useEffect, useState } from 'react'
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
import type { KnobEntry, ActivePanelsMap } from '../types'
import type { IDockviewPanelProps } from 'dockview-react'

function KnobView({ cc, label, value, onClick, isSelected, isDragging, style, setNodeRef, attributes, listeners }: {
  cc: number
  label: string
  value: number
  onClick: () => void
  isSelected?: boolean
  isDragging?: boolean
  style?: React.CSSProperties
  setNodeRef?: (el: HTMLElement | null) => void
  attributes?: Record<string, unknown>
  listeners?: Record<string, unknown>
}) {
  const pct = Math.round((value / 127) * 100)
  const angle = (value / 127) * 270 - 135
  return (
    <div
      ref={setNodeRef}
      className={`knob${isDragging ? ' dragging' : ''}${isSelected ? ' selected' : ''}`}
      style={style}
      onClick={onClick}
      title={`CC ${cc}`}
      {...attributes}
      {...listeners}
    >
      <div className="knob-visual">
        <div className="knob-needle" style={{ transform: `rotate(${angle}deg)` }} />
      </div>
      <div className="knob-label">{label}</div>
      <div className="knob-value">{pct}%</div>
    </div>
  )
}

function SortableKnob({ knob, onClick, isSelected }: {
  knob: KnobEntry
  onClick: () => void
  isSelected?: boolean
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: knob.cc })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : undefined,
    zIndex: isDragging ? 10 : undefined,
  }

  return (
    <KnobView
      cc={knob.cc}
      label={knob.label}
      value={knob.value}
      onClick={onClick}
      isSelected={isSelected}
      isDragging={isDragging}
      style={style}
      setNodeRef={setNodeRef}
      attributes={attributes as unknown as Record<string, unknown>}
      listeners={listeners as unknown as Record<string, unknown>}
    />
  )
}

export function KnobPanel(props: IDockviewPanelProps) {
  const instanceId = props.api.id
  const panel = useAppStore(s => s.panels[instanceId])
  const knobs = useAppStore(s => s.knobs)
  const knobPresets = useAppStore(s => s.knobPresets ?? [])
  const activePanels = useAppStore(s => s.activePanels)
  const selectKnob = useAppStore(s => s.selectKnob)
  const selectedKnobCC = useAppStore(s => s.selectedKnobCC)

  // Derive this panel's knob list from its preset (independent of global knobs)
  const presetName = panel?.preset ?? ''
  const presetKnobs = knobPresets.find(kp => kp.name === presetName)
  // Fall back to global knobs when the panel references an unknown preset
  const isActive = panel
    ? activePanels[`${panel.type}:${panel.bank}` as keyof ActivePanelsMap] === instanceId
    : false
  const baseKnobs: KnobEntry[] = presetKnobs
    ? (presetKnobs as unknown as { knobs?: KnobEntry[] }).knobs ?? []
    : knobs
  // For the active panel we trust the live `knobs` (they match the active preset);
  // for inactive panels we derive a best-effort display from catalog (preset on store).
  const displayKnobs: KnobEntry[] = isActive ? knobs : baseKnobs.length ? baseKnobs : knobs

  const [activeId, setActiveId] = useState<number | null>(null)
  const [orderedCCs, setOrderedCCs] = useState(() => displayKnobs.map(k => k.cc))

  useEffect(() => {
    setOrderedCCs(displayKnobs.map(k => k.cc))
  }, [displayKnobs.length, presetName])

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
    const ccA = active.id as number
    const ccB = over.id as number
    setOrderedCCs(prev => {
      const next = [...prev]
      const iA = next.indexOf(ccA)
      const iB = next.indexOf(ccB)
      if (iA !== -1 && iB !== -1) [next[iA], next[iB]] = [next[iB], next[iA]]
      return next
    })
    if (!isActive) return // only active panel persists swaps (global knobs model)
    const currentKnobs = useAppStore.getState().knobs
    const idxA = currentKnobs.findIndex(k => k.cc === ccA)
    const idxB = currentKnobs.findIndex(k => k.cc === ccB)
    if (idxA !== -1 && idxB !== -1) {
      const swapped = [...currentKnobs]
      swapped[idxA] = { ...currentKnobs[idxB], cc: ccA }
      swapped[idxB] = { ...currentKnobs[idxA], cc: ccB }
      useAppStore.setState({ knobs: swapped })
    }
    fetch('/api/knobs/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cc_a: ccA, cc_b: ccB }),
    }).catch((e) => console.error(`[KnobPanel] swap failed:`, e))
  }, [isActive])

  const activeKnob = activeId != null ? displayKnobs.find(k => k.cc === activeId) : undefined

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
        <SortableContext items={orderedCCs} strategy={rectSortingStrategy}>
          <div className="knob-grid responsive">
            {orderedCCs.map(cc => {
              const k = displayKnobs.find(kn => kn.cc === cc)
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
            <KnobView
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
