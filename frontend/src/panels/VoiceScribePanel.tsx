import { useState, useEffect, useRef } from 'react'
import type { IDockviewPanelProps } from 'dockview-react'

interface VsState {
  active: boolean
  recording: boolean
  processing: boolean
  status: string
  last_original: string
  last_result: string
  last_prompt_label: string
  chat_model: string
  transcription_model: string
  input_language: string
  output_language: string
  prompts: { label: string; description: string }[]
  chat_history_length: number
}

const DEFAULT: VsState = {
  active: false, recording: false, processing: false,
  status: 'Idle', last_original: '', last_result: '',
  last_prompt_label: '', chat_model: '', transcription_model: '',
  input_language: '', output_language: '',
  prompts: [], chat_history_length: 0,
}

export function VoiceScribePanel(_props: IDockviewPanelProps) {
  const [vs, setVs] = useState<VsState>(DEFAULT)
  const intervalRef = useRef<number | null>(null)

  useEffect(() => {
    function fetchState() {
      fetch('/api/voice-scribe/state')
        .then(r => r.ok ? r.json() : DEFAULT)
        .then(setVs)
        .catch(() => {})
    }
    fetchState()
    intervalRef.current = window.setInterval(fetchState, 2000)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [])

  const statusColor = vs.recording ? '#FF7878' : vs.processing ? '#FFC85A' : '#5AE68C'

  return (
    <div className="vs-panel-content">
      <div className="pp-header">
        <strong>Voice Scribe</strong>
        <span className={`pp-status ${vs.active ? 'ok' : ''}`}>
          {vs.active ? 'Active' : 'Inactive'}
        </span>
      </div>

      {/* Status */}
      <div className="vs-status-bar" style={{ borderLeftColor: statusColor }}>
        <span className="vs-status-text">{vs.status}</span>
        {vs.recording && <span className="vs-recording-dot" />}
      </div>

      {/* Last result */}
      {vs.last_result && (
        <div className="vs-section">
          <div className="vm-section-title">Last Result</div>
          {vs.last_prompt_label && (
            <div className="vs-prompt-tag">Prompt: {vs.last_prompt_label}</div>
          )}
          {vs.last_original && (
            <div className="vs-text-block original">
              <span className="vs-text-label">Original:</span>
              <span className="vs-text">{vs.last_original}</span>
            </div>
          )}
          <div className="vs-text-block result">
            <span className="vs-text-label">Result:</span>
            <span className="vs-text">{vs.last_result}</span>
          </div>
        </div>
      )}

      {/* Prompts */}
      <div className="vs-section">
        <div className="vm-section-title">Prompts ({vs.prompts.length})</div>
        <div className="vs-prompts-list">
          {vs.prompts.map((p, i) => (
            <div key={i} className="vs-prompt-item">
              <span className="vs-prompt-name">{p.label}</span>
              <span className="vs-prompt-desc">{p.description}</span>
            </div>
          ))}
          {vs.prompts.length === 0 && (
            <div className="vs-empty-hint">No prompts loaded</div>
          )}
        </div>
      </div>

      {/* Chat */}
      <div className="vs-section">
        <div className="vm-section-title">Chat</div>
        <div className="settings-row">
          <span className="settings-label">Messages</span>
          <span className="settings-value">{vs.chat_history_length}</span>
        </div>
        <button className="settings-btn" onClick={() => {
          fetch('/api/voice-scribe/new-chat', { method: 'POST' })
        }}>
          New Chat
        </button>
      </div>

      {/* Config */}
      <div className="vs-section">
        <div className="vm-section-title">Config</div>
        <div className="settings-row">
          <span className="settings-label">Chat model</span>
          <span className="settings-value">{vs.chat_model || '—'}</span>
        </div>
        <div className="settings-row">
          <span className="settings-label">Whisper</span>
          <span className="settings-value">{vs.transcription_model || '—'}</span>
        </div>
        <div className="settings-row">
          <span className="settings-label">Language</span>
          <span className="settings-value">{vs.input_language} → {vs.output_language}</span>
        </div>
      </div>
    </div>
  )
}
