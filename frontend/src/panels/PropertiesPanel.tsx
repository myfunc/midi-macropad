import { useState } from 'react'
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

export function PropertiesPanel(_props: IDockviewPanelProps) {
  const selectedNote = useAppStore(s => s.selectedNote)
  const pads = useAppStore(s => s.pads)

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
    // TODO: wire to PATCH /api/pads/{note}
    console.log('Save label:', selectedNote, value)
  }

  function assignAction(actionId: string) {
    // TODO: wire to PATCH /api/pads/{note}
    console.log('Assign action:', selectedNote, actionId)
  }

  function clearAction() {
    // TODO: wire to DELETE /api/pads/{note}/action
    console.log('Clear action:', selectedNote)
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

      <div className="props-section">
        <label className="props-label">Label</label>
        <input
          className="props-input"
          type="text"
          key={selectedNote}
          defaultValue={entry.label}
          placeholder="Pad label..."
          readOnly={entry.locked}
          onBlur={(e) => saveLabel(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
        />
      </div>

      <div className="props-section">
        <label className="props-label">Hotkey</label>
        <input
          className="props-input"
          type="text"
          key={`hk-${selectedNote}`}
          defaultValue={entry.hotkey}
          placeholder="Click to set hotkey..."
          readOnly={entry.locked}
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
                  className={`action-chip ${a.cls} ${entry.action_type === a.id ? 'active' : ''}`}
                  onClick={() => !entry.locked && assignAction(a.id)}
                >
                  {a.name}
                </div>
              ))}
            </div>
          </div>
        ))}
        {!entry.locked && (
          <button className="btn-clear" onClick={clearAction}>Clear Assignment</button>
        )}
      </div>

      {entry.locked && (
        <div className="props-locked">
          &#128274; Managed by plugin — some fields read-only
        </div>
      )}
    </div>
  )
}
