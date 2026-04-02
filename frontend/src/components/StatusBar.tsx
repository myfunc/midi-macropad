import { useAppStore } from '../stores/useAppStore'

export function StatusBar() {
  const midiConnected = useAppStore(s => s.midiConnected)
  const midiPortName = useAppStore(s => s.midiPortName)
  const obs = useAppStore(s => s.obs)

  return (
    <div className="status-bar">
      <div className="status-item">
        <span className={`status-dot ${midiConnected ? 'green' : 'red'}`} />
        MIDI: {midiPortName || 'disconnected'}
      </div>
      <div className="status-item">
        <span className={`status-dot ${obs.connected ? 'green' : 'red'}`} />
        OBS: {obs.connected ? 'OK' : 'off'}
      </div>
      {obs.current_scene && (
        <div className="status-item" style={{ color: '#64B4FF' }}>
          Scene: {obs.current_scene}
        </div>
      )}
      {obs.is_recording && (
        <div className="status-item">
          <span className="status-dot yellow" />
          Recording
        </div>
      )}
      <div className="status-spacer" />
      <div className="status-item" style={{ color: '#707088' }}>Web UI v1.0</div>
    </div>
  )
}
