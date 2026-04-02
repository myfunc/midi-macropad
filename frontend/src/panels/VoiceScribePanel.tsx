import { useState, useEffect, useRef } from 'react'
import { useAppStore } from '../stores/useAppStore'
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
  mic_device: number | null
}

const DEFAULT: VsState = {
  active: false, recording: false, processing: false,
  status: 'Idle', last_original: '', last_result: '',
  last_prompt_label: '', chat_model: '', transcription_model: '',
  input_language: '', output_language: '',
  prompts: [], chat_history_length: 0, mic_device: null,
}

type Phase = 'idle' | 'recording' | 'processing'

function getPhase(vs: VsState): Phase {
  if (vs.recording) return 'recording'
  if (vs.processing) return 'processing'
  return 'idle'
}

const PHASE_STYLES: Record<Phase, { color: string; bg: string; label: string }> = {
  idle:       { color: '#5AE68C', bg: 'rgba(90,230,140,0.08)',  label: 'Ready' },
  recording:  { color: '#FF7878', bg: 'rgba(255,120,120,0.08)', label: 'Recording' },
  processing: { color: '#FFC85A', bg: 'rgba(255,200,90,0.08)',  label: 'Processing' },
}

export function VoiceScribePanel(_props: IDockviewPanelProps) {
  const [vs, setVs] = useState<VsState>(DEFAULT)
  const showToast = useAppStore(s => s.showToast)
  const intervalRef = useRef<number | null>(null)

  useEffect(() => {
    function fetchState() {
      fetch('/api/voice-scribe/state')
        .then(r => r.ok ? r.json() : DEFAULT)
        .then(setVs)
        .catch(() => {})
    }
    fetchState()
    intervalRef.current = window.setInterval(fetchState, 1500)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [])

  const phase = getPhase(vs)
  const ps = PHASE_STYLES[phase]

  function newChat() {
    fetch('/api/voice-scribe/new-chat', { method: 'POST' })
      .then(() => showToast('New chat started'))
  }

  return (
    <div className="vs-root">
      {/* ── Header ── */}
      <div className="vs-header">
        <div className="vs-header-left">
          <span className="vs-title">Voice Scribe</span>
          <span className="vs-lang-badge">{vs.input_language} → {vs.output_language}</span>
        </div>
        <span className={`pp-status ${vs.active ? 'ok' : 'err'}`}>
          {vs.active ? 'Active' : 'Off'}
        </span>
      </div>

      {/* ── Phase indicator ── */}
      <div className="vs-phase" style={{ borderLeftColor: ps.color, background: ps.bg }}>
        <div className="vs-phase-row">
          {phase === 'recording' && <span className="vs-rec-dot" />}
          <span className="vs-phase-label" style={{ color: ps.color }}>{ps.label}</span>
          <span className="vs-phase-detail">{vs.status}</span>
        </div>
        {vs.last_prompt_label && phase !== 'idle' && (
          <span className="vs-phase-prompt">Prompt: {vs.last_prompt_label}</span>
        )}
      </div>

      {/* ── Last output ── */}
      {vs.last_result ? (
        <div className="vs-output">
          <div className="vs-output-header">
            <span className="vs-output-title">Last Output</span>
            {vs.last_prompt_label && (
              <span className="vs-output-prompt">{vs.last_prompt_label}</span>
            )}
          </div>
          {vs.last_original && (
            <div className="vs-output-block vs-orig">
              <div className="vs-output-label">Spoken (original)</div>
              <div className="vs-output-text">{vs.last_original}</div>
            </div>
          )}
          <div className="vs-output-block vs-result">
            <div className="vs-output-label">Result</div>
            <div className="vs-output-text">{vs.last_result}</div>
          </div>
        </div>
      ) : (
        <div className="vs-empty-output">
          <span className="vs-empty-icon">&#127908;</span>
          <span>Press a prompt pad to start recording</span>
        </div>
      )}

      {/* ── Chat ── */}
      <div className="vs-chat-bar">
        <span className="vs-chat-info">
          Chat: {vs.chat_history_length} message{vs.chat_history_length !== 1 ? 's' : ''}
        </span>
        <button className="vs-new-chat-btn" onClick={newChat}>New Chat</button>
      </div>

      {/* ── Prompt pads ── */}
      <div className="vs-section-title">Prompt Pads</div>
      <div className="vs-prompt-grid">
        {vs.prompts.map((p, i) => (
          <div key={i} className="vs-prompt-card">
            <span className="vs-prompt-card-name">{p.label}</span>
            {p.description && (
              <span className="vs-prompt-card-desc">{p.description}</span>
            )}
          </div>
        ))}
        {vs.prompts.length === 0 && (
          <div className="vs-empty-hint">No prompts loaded — check prompts.toml</div>
        )}
      </div>

      {/* ── Models ── */}
      <div className="vs-section-title">Models</div>
      <div className="vs-models">
        <div className="vs-model-row">
          <span className="vs-model-label">Chat</span>
          <span className="vs-model-value">{vs.chat_model || '—'}</span>
        </div>
        <div className="vs-model-row">
          <span className="vs-model-label">Whisper</span>
          <span className="vs-model-value">{vs.transcription_model || '—'}</span>
        </div>
        {vs.mic_device !== null && (
          <div className="vs-model-row">
            <span className="vs-model-label">Mic</span>
            <span className="vs-model-value">Device #{vs.mic_device}</span>
          </div>
        )}
      </div>
    </div>
  )
}
