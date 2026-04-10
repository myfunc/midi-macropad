import { useState, useCallback, useRef, useEffect } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

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

function patchPad(note: number, data: Record<string, unknown>): Promise<Response> {
  return fetch(`/api/pads/${note}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function PropertiesPanel(_props: IDockviewPanelProps) {
  const selectedNote = useAppStore(s => s.selectedNote)
  const pads = useAppStore(s => s.pads)
  const showToast = useAppStore(s => s.showToast)
  const [capturing, setCapturing] = useState(false)
  const [hkSaving, setHkSaving] = useState(false)
  const hotkeyRef = useRef<HTMLInputElement>(null)

  // Reset capture mode when pad changes
  useEffect(() => { setCapturing(false) }, [selectedNote])

  const refreshPads = useCallback(() => {
    fetch('/api/pads').then(r => r.json()).then(data => {
      useAppStore.getState().updatePads(data)
    }).catch(() => {})
  }, [])

  if (selectedNote === null) {
    return (
      <div className="props-empty">
        <div className="props-empty-icon">&#127899;</div>
        <div className="props-empty-hint">
          Select a pad to view<br />and edit its properties
        </div>
      </div>
    )
  }

  const entry = pads[String(selectedNote)]
  if (!entry) {
    return <div className="props-empty"><div className="props-empty-hint">Pad not found</div></div>
  }

  const padIndex = selectedNote - (selectedNote >= 24 ? 24 : 16) + 1
  const bank = selectedNote >= 24 ? 'B' : 'A'

  function saveLabel(value: string) {
    patchPad(selectedNote!, { label: value.trim() }).then(() => refreshPads())
  }

  function commitHotkey(hotkey: string) {
    setHkSaving(true)
    patchPad(selectedNote!, { hotkey })
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
    patchPad(selectedNote!, { action: { type: actionId } }).then(() => {
      showToast(`Action: ${actionId}`)
      refreshPads()
    })
  }

  function clearAction() {
    fetch(`/api/pads/${selectedNote}/action`, { method: 'DELETE' }).then(() => {
      showToast('Assignment cleared')
      refreshPads()
    })
  }

  return (
    <div className="properties-content">
      <div className="props-header">
        <span className="pad-badge">Pad {bank}{padIndex}</span>
        <span className="props-note">note {selectedNote}</span>
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
          key={`label-${selectedNote}`}
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
            key={`hk-${selectedNote}`}
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
