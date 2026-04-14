import { useState, useCallback, useRef, useEffect } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { KnobPropertiesView } from '../components/KnobEditorDialog'
import type { IDockviewPanelProps } from 'dockview-react'
import type { PadAction } from '../types'

const ACTION_GROUPS = [
  {
    label: 'System',
    actions: [
      { id: 'keystroke', name: 'Key', cls: 'system' },
      { id: 'app_keystroke', name: 'AppKey', cls: 'system' },
      { id: 'shell', name: 'Shell', cls: 'system' },
      { id: 'launch', name: 'Launch', cls: 'system' },
      { id: 'volume', name: 'Vol', cls: 'system' },
      { id: 'scroll', name: 'Scroll', cls: 'system' },
    ],
  },
  {
    label: 'OBS',
    actions: [
      { id: 'scene_screen', name: 'Screen', cls: 'scene' },
      { id: 'scene_camera', name: 'Camera', cls: 'scene' },
      { id: 'scene_pip', name: 'PiP', cls: 'scene' },
      { id: 'scene_cam_app', name: 'Cam+App', cls: 'scene' },
      { id: 'mute_mic', name: 'Mute Mic', cls: 'mute' },
      { id: 'mute_desktop', name: 'Mute Desk', cls: 'mute' },
      { id: 'mute_aux', name: 'Mute AUX', cls: 'mute' },
      { id: 'start_session', name: 'Session', cls: 'session' },
      { id: 'toggle_record', name: 'Record', cls: 'record' },
      { id: 'save_replay', name: 'Replay', cls: 'replay' },
    ],
  },
  {
    label: 'Voicemeeter',
    actions: [
      { id: 'mic_mute', name: 'Mic Mute', cls: 'plugin' },
      { id: 'desk_mute', name: 'Desk Mute', cls: 'plugin' },
      { id: 'eq_toggle', name: 'EQ', cls: 'plugin' },
      { id: 'gate', name: 'Gate', cls: 'plugin' },
    ],
  },
]

function keyEventToHotkey(e: React.KeyboardEvent): string | null {
  const key = e.key
  if (['Control', 'Shift', 'Alt', 'Meta'].includes(key)) return null
  const parts: string[] = []
  if (e.ctrlKey) parts.push('ctrl')
  if (e.altKey) parts.push('alt')
  if (e.shiftKey) parts.push('shift')
  const keyName = key.length === 1 ? key.toLowerCase() : key.toLowerCase()
    .replace('arrowup', 'up').replace('arrowdown', 'down')
    .replace('arrowleft', 'left').replace('arrowright', 'right')
    .replace(' ', 'space').replace('escape', 'esc')
  parts.push(keyName)
  return parts.join('+')
}

const MOUSE_MAP: Record<number, string> = {
  1: 'mouse3', 3: 'mouse4', 4: 'mouse5',
}

/** Parse composite pad key "PresetName:note" into [presetName, note] */
function parsePadKey(key: string): [string, number] {
  const colonIdx = key.lastIndexOf(':')
  const presetName = key.substring(0, colonIdx)
  const note = parseInt(key.substring(colonIdx + 1), 10)
  return [presetName, note]
}

function patchPad(presetName: string, note: number, data: Record<string, unknown>): Promise<Response> {
  return fetch(`/api/presets/${encodeURIComponent(presetName)}/pads/${note}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function PropertiesPanel(_props: IDockviewPanelProps) {
  const selectedPadKey = useAppStore(s => s.selectedPadKey)
  const selectedKnobCC = useAppStore(s => s.selectedKnobCC)
  const selectedPianoNote = useAppStore(s => s.selectedPianoNote)
  const pads = useAppStore(s => s.pads)
  const showToast = useAppStore(s => s.showToast)
  const [capturing, setCapturing] = useState(false)
  const [hkSaving, setHkSaving] = useState(false)
  const hotkeyRef = useRef<HTMLInputElement>(null)

  // Reset capture mode when pad changes
  useEffect(() => { setCapturing(false) }, [selectedPadKey])

  const refreshPads = useCallback(() => {
    fetch('/api/pads').then(r => r.json()).then(data => {
      useAppStore.getState().updatePads(data)
    }).catch(() => {})
  }, [])

  // Show knob properties when a knob is selected
  if (selectedKnobCC !== null) {
    return <KnobPropertiesView cc={selectedKnobCC} />
  }

  // Show piano key mapping editor when a piano note is selected in map mode.
  if (selectedPianoNote !== null) {
    return <PianoKeyPropertiesView note={selectedPianoNote} />
  }

  if (selectedPadKey === null) {
    return (
      <div className="props-empty">
        <div className="props-empty-icon">&#127899;</div>
        <div className="props-empty-hint">
          Select a pad, knob, or piano key<br />to view and edit its properties
        </div>
      </div>
    )
  }

  const [presetName, noteNum] = parsePadKey(selectedPadKey)
  const entry = pads[selectedPadKey]
  if (!entry) {
    return <div className="props-empty"><div className="props-empty-hint">Pad not found</div></div>
  }

  const padIndex = noteNum - (noteNum >= 24 ? 24 : 16) + 1
  const bank = noteNum >= 24 ? 'B' : 'A'

  function saveLabel(value: string) {
    patchPad(presetName, noteNum, { label: value.trim() }).then(() => refreshPads())
  }

  function commitHotkey(hotkey: string) {
    setHkSaving(true)
    patchPad(presetName, noteNum, { hotkey })
      .then(async (r) => {
        if (r.ok) {
          showToast(hotkey ? `Hotkey: ${hotkey}` : 'Hotkey cleared')
        } else {
          const err = await r.json().catch(() => ({ error: r.statusText }))
          showToast(`Error: ${err.error || r.status}`)
        }
        refreshPads()
      })
      .catch((e) => showToast(`Network error: ${e.message}`))
      .finally(() => setHkSaving(false))
  }

  function assignAction(actionId: string) {
    patchPad(presetName, noteNum, { action: { type: actionId } }).then(() => {
      showToast(`Action: ${actionId}`)
      refreshPads()
    })
  }

  function clearAction() {
    fetch(`/api/presets/${encodeURIComponent(presetName)}/pads/${noteNum}/action`, { method: 'DELETE' }).then(() => {
      showToast('Assignment cleared')
      refreshPads()
    })
  }

  return (
    <div className="properties-content">
      <div className="props-header">
        <span className="pad-badge">Pad {bank}{padIndex}</span>
        <span className="props-note">note {noteNum}</span>
        {presetName && <span style={{ fontSize: 11, color: '#8899aa', marginLeft: 'auto' }}>{presetName}</span>}
      </div>
      <div className="props-current">
        Current: <strong>{entry.label || 'Not assigned'}</strong>
      </div>

      {/* Label */}
      <div className="props-section">
        <label className="props-label">Label</label>
        <input
          className="props-input"
          type="text"
          key={`label-${selectedPadKey}`}
          defaultValue={entry.label}
          placeholder="Pad label..."
          onBlur={(e) => saveLabel(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
        />
      </div>

      {/* Hotkey */}
      <div className="props-section">
        <label className="props-label">Hotkey</label>
        <div className="props-hotkey-row">
          <input
            ref={hotkeyRef}
            className={`props-input ${capturing ? 'props-hotkey-input capturing' : ''}`}
            type="text"
            key={`hk-${selectedPadKey}`}
            defaultValue={entry.hotkey || ''}
            placeholder="mouse5, ctrl+shift+r..."
            readOnly={capturing}
            onKeyDown={(e) => {
              if (capturing) {
                e.preventDefault()
                e.stopPropagation()
                if (e.key === 'Escape') { setCapturing(false); return }
                const hk = keyEventToHotkey(e)
                if (hk) {
                  setCapturing(false)
                  if (hotkeyRef.current) hotkeyRef.current.value = hk
                  commitHotkey(hk)
                }
              }
            }}
            onMouseDown={(e) => {
              if (capturing && e.button >= 1) {
                e.preventDefault()
                const hk = MOUSE_MAP[e.button] || `mouse${e.button + 1}`
                setCapturing(false)
                if (hotkeyRef.current) hotkeyRef.current.value = hk
                commitHotkey(hk)
              }
            }}
            onContextMenu={(e) => { if (capturing) e.preventDefault() }}
          />
          <button
            className="props-hotkey-save"
            disabled={hkSaving}
            onClick={() => {
              const val = hotkeyRef.current?.value.trim() || ''
              commitHotkey(val)
            }}
            title="Save hotkey"
          >Save</button>
          <button
            className={`props-hotkey-mode-btn ${capturing ? 'active' : ''}`}
            onClick={() => setCapturing(!capturing)}
            title={capturing ? 'Cancel capture' : 'Capture key/mouse'}
          >{capturing ? '\u2715' : '\uD83C\uDFA7'}</button>
          {entry.hotkey && (
            <button className="props-hotkey-clear" onClick={() => {
              if (hotkeyRef.current) hotkeyRef.current.value = ''
              commitHotkey('')
            }} title="Clear hotkey">&times;</button>
          )}
        </div>
        {capturing && (
          <div className="props-hotkey-hint">Press any key/mouse button, Esc to cancel</div>
        )}
      </div>

      {/* Actions */}
      <div className="props-section">
        <div className="props-section-title">Assign Action</div>
        {ACTION_GROUPS.map(group => (
          <div key={group.label} className="action-group">
            <div className="action-group-label">{group.label}</div>
            <div className="action-grid">
              {group.actions.map(a => (
                <div
                  key={a.id}
                  className={`action-chip ${a.cls} ${entry.action_type === a.id ? 'active' : ''}`}
                  onClick={() => assignAction(a.id)}
                >
                  {a.name}
                </div>
              ))}
            </div>
          </div>
        ))}
        <button className="btn-clear" onClick={clearAction}>Clear Assignment</button>
      </div>

      {entry.locked && (
        <div className="props-locked">
          &#128274; Managed by plugin — some fields read-only
        </div>
      )}
    </div>
  )
}

/* ── Piano key properties (map bank) ── */

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

function midiNoteName(note: number): string {
  const octave = Math.floor(note / 12) - 1
  return `${NOTE_NAMES[note % 12]}${octave}`
}

/** Which action fields are relevant to a given action type. */
function fieldsForActionType(type: string): Array<keyof PadAction> {
  switch (type) {
    case 'keystroke':
    case 'app_keystroke':
      return ['keys']
    case 'shell':
      return ['command']
    case 'launch':
      return ['process']
    case 'volume':
    case 'scroll':
      return ['target']
    case 'plugin':
    case 'plugin_action':
      return ['target', 'command']
    default:
      // OBS / Voicemeeter actions don't need extra fields — they're pure
      // type-selected triggers resolved server-side.
      return []
  }
}

const FIELD_PLACEHOLDERS: Record<string, string> = {
  keys: 'ctrl+shift+r',
  target: 'e.g. mic / desktop / plugin:command',
  command: 'e.g. pipeline:start',
  process: 'C:/path/to/app.exe',
}

function PianoKeyPropertiesView({ note }: { note: number }) {
  const panels = useAppStore(s => s.panels)
  const activePanels = useAppStore(s => s.activePanels)
  const pianoPresets = useAppStore(s => s.pianoPresets)
  const updatePianoKey = useAppStore(s => s.updatePianoKey)
  const showToast = useAppStore(s => s.showToast)

  // Resolve the active 'piano:map' panel to determine target preset.
  const mapPanelId = activePanels['piano:map'] ?? null
  const mapPanel = mapPanelId ? panels[mapPanelId] : null
  const presetName = mapPanel?.preset ?? ''
  const preset = pianoPresets.find(p => p.name === presetName) ?? null
  const mapping = preset?.keys.find(k => k.note === note) ?? null

  const [labelDraft, setLabelDraft] = useState(mapping?.label ?? '')
  // Draft action mirrors mapping.action so users can tweak per-type fields
  // (keys, target, command, process) before persisting. Persisted on blur.
  const [actionDraft, setActionDraft] = useState<PadAction | null>(
    mapping?.action ? { ...mapping.action } : null,
  )

  useEffect(() => {
    setLabelDraft(mapping?.label ?? '')
    setActionDraft(mapping?.action ? { ...mapping.action } : null)
  }, [note, mapping?.label, mapping?.action])

  if (!mapPanel) {
    return (
      <div className="props-empty">
        <div className="props-empty-hint">
          No active Piano (Map) panel.<br />
          Activate a Piano map panel to edit key mappings.
        </div>
      </div>
    )
  }

  function commitLabel(value: string) {
    if (!presetName) return
    updatePianoKey(presetName, note, { label: value.trim() || undefined })
  }

  function assignActionType(actionId: string) {
    if (!presetName) return
    // Preserve relevant fields from the existing draft where the new type
    // still uses them (e.g. switching keystroke -> app_keystroke keeps keys).
    const next: PadAction = { type: actionId }
    const allowedFields = new Set(fieldsForActionType(actionId))
    if (actionDraft) {
      for (const f of allowedFields) {
        const v = (actionDraft as any)[f]
        if (typeof v === 'string' && v) (next as any)[f] = v
      }
    }
    setActionDraft(next)
    updatePianoKey(presetName, note, { action: next })
      .then(() => showToast(`Action: ${actionId}`))
  }

  function commitActionField(field: keyof PadAction, value: string) {
    if (!presetName || !actionDraft) return
    const trimmed = value.trim()
    const next: PadAction = { ...actionDraft }
    if (trimmed) {
      ;(next as any)[field] = trimmed
    } else {
      delete (next as any)[field]
    }
    setActionDraft(next)
    updatePianoKey(presetName, note, { action: next })
  }

  function clearAction() {
    if (!presetName) return
    setActionDraft(null)
    updatePianoKey(presetName, note, { action: null })
      .then(() => showToast('Action cleared'))
  }

  const currentActionType = actionDraft?.type ?? ''
  const activeFields = currentActionType
    ? fieldsForActionType(currentActionType)
    : []

  return (
    <div className="properties-content">
      <div className="props-header">
        <span className="pad-badge">Piano {midiNoteName(note)}</span>
        <span className="props-note">note {note}</span>
        {presetName && (
          <span style={{ fontSize: 11, color: '#8899aa', marginLeft: 'auto' }}>
            {presetName}
          </span>
        )}
      </div>
      <div className="props-current">
        Current: <strong>{mapping?.label || 'Not assigned'}</strong>
      </div>

      <div className="props-section">
        <label className="props-label">Label</label>
        <input
          className="props-input"
          type="text"
          key={`pk-label-${note}`}
          value={labelDraft}
          placeholder="Key label..."
          onChange={e => setLabelDraft(e.target.value)}
          onBlur={e => commitLabel(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          }}
        />
      </div>

      <div className="props-section">
        <div className="props-section-title">Assign Action</div>
        {ACTION_GROUPS.map(group => (
          <div key={group.label} className="action-group">
            <div className="action-group-label">{group.label}</div>
            <div className="action-grid">
              {group.actions.map(a => (
                <div
                  key={a.id}
                  className={`action-chip ${a.cls} ${currentActionType === a.id ? 'active' : ''}`}
                  onClick={() => assignActionType(a.id)}
                >
                  {a.name}
                </div>
              ))}
            </div>
          </div>
        ))}

        {activeFields.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {activeFields.map(field => (
              <div key={field} className="props-section" style={{ marginTop: 6 }}>
                <label className="props-label">
                  {field.charAt(0).toUpperCase() + field.slice(1)}
                </label>
                <input
                  className="props-input"
                  type="text"
                  key={`pk-${note}-${currentActionType}-${field}`}
                  defaultValue={(actionDraft as any)?.[field] ?? ''}
                  placeholder={FIELD_PLACEHOLDERS[field as string] ?? ''}
                  onBlur={e => commitActionField(field, e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  }}
                />
              </div>
            ))}
          </div>
        )}

        <button className="btn-clear" onClick={clearAction}>Clear Assignment</button>
      </div>
    </div>
  )
}
