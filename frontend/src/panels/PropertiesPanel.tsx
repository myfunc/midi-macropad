import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

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
          defaultValue={entry.label}
          placeholder="Pad label..."
        />
      </div>

      <div className="props-section">
        <label className="props-label">Action Type</label>
        <div className="props-value">{entry.action_type || '—'}</div>
      </div>

      {entry.action_data && Object.keys(entry.action_data).length > 0 && (
        <div className="props-section">
          <label className="props-label">Action Data</label>
          {Object.entries(entry.action_data).map(([k, v]) => (
            <div key={k} className="props-row">
              <span className="props-key">{k}:</span>
              <span className="props-val">{v}</span>
            </div>
          ))}
        </div>
      )}

      {entry.hotkey && (
        <div className="props-section">
          <label className="props-label">Hotkey</label>
          <div className="props-value">{entry.hotkey}</div>
        </div>
      )}

      {entry.locked && (
        <div className="props-locked">
          &#128274; Managed by plugin — read-only
        </div>
      )}
    </div>
  )
}
