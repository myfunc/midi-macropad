import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

export function SettingsPanel(_props: IDockviewPanelProps) {
  const midiConnected = useAppStore(s => s.midiConnected)
  const midiPortName = useAppStore(s => s.midiPortName)
  const plugins = useAppStore(s => s.plugins)

  return (
    <div className="settings-panel-content">
      <div className="settings-section">
        <div className="settings-section-title">MIDI Device</div>
        <div className="settings-row">
          <span className="settings-label">Status</span>
          <span className={`pp-status ${midiConnected ? 'ok' : 'err'}`}>
            {midiConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        <div className="settings-row">
          <span className="settings-label">Port</span>
          <span className="settings-value">{midiPortName || '—'}</span>
        </div>
        <button className="settings-btn" onClick={() => fetch('/api/midi/reconnect', { method: 'POST' })}>
          Reconnect MIDI
        </button>
      </div>

      <div className="settings-section">
        <div className="settings-section-title">Plugins</div>
        {plugins.map(p => (
          <div key={p.name} className="settings-row">
            <span className="settings-label">{p.name}</span>
            <span className={`pp-status ${p.enabled ? 'ok' : ''}`}>
              {p.enabled ? 'Loaded' : 'Off'}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
