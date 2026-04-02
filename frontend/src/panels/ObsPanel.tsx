import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

export function ObsPanel(_props: IDockviewPanelProps) {
  const obs = useAppStore(s => s.obs)

  return (
    <div className="plugin-panel">
      <div className="pp-header">
        <strong>OBS Session</strong>
        <span className={`pp-status ${obs.connected ? 'ok' : 'err'}`}>
          {obs.connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>
      <div className="pp-row"><span className="pp-label">Scene</span><span className="pp-value">{obs.current_scene || '—'}</span></div>
      <div className="pp-row">
        <span className="pp-label">Recording</span>
        <span className="pp-value" style={{ color: obs.is_recording ? '#FF7878' : undefined }}>
          {obs.is_recording ? 'Active' : 'Stopped'}
        </span>
      </div>
      <div className="pp-row">
        <span className="pp-label">Streaming</span>
        <span className="pp-value" style={{ color: obs.is_streaming ? '#FF7878' : undefined }}>
          {obs.is_streaming ? 'Live' : 'Off'}
        </span>
      </div>
      <div className="pp-row">
        <span className="pp-label">Replay Buffer</span>
        <span className="pp-value" style={{ color: obs.is_replay_buffer_active ? '#5AE68C' : undefined }}>
          {obs.is_replay_buffer_active ? 'Ready' : 'Off'}
        </span>
      </div>
      <div className="pp-row">
        <span className="pp-label">Scenes</span>
        <span className="pp-value">{obs.scenes?.length ?? 0}</span>
      </div>
    </div>
  )
}
