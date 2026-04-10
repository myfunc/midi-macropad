import { useState, useEffect, useRef, useCallback } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

interface PromptData {
  pad: number
  label: string
  system: string
}

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
  prompts: PromptData[]
  chat_history_length: number
  mic_device: number | null
  whisper_prompt: string
}

const DEFAULT: VsState = {
  active: false, recording: false, processing: false,
  status: 'Idle', last_original: '', last_result: '',
  last_prompt_label: '', chat_model: '', transcription_model: '',
  prompts: [], chat_history_length: 0, mic_device: null,
  whisper_prompt: '',
}

const SPECIAL_LABELS = ['New Chat', 'Context', 'Speak', 'Cancel']

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
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editSystem, setEditSystem] = useState('')
  const [addingNew, setAddingNew] = useState(false)
  const [newLabel, setNewLabel] = useState('')
  const [newSystem, setNewSystem] = useState('')
  const [saving, setSaving] = useState(false)
  const showToast = useAppStore(s => s.showToast)
  const intervalRef = useRef<number | null>(null)

  const fetchState = useCallback(() => {
    fetch('/api/voice-scribe/state')
      .then(r => r.ok ? r.json() : DEFAULT)
      .then(setVs)
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchState()
    intervalRef.current = window.setInterval(fetchState, 1500)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [fetchState])

  const phase = getPhase(vs)
  const ps = PHASE_STYLES[phase]

  function newChat() {
    fetch('/api/voice-scribe/new-chat', { method: 'POST' })
      .then(() => showToast('New chat started'))
  }

  function startEdit(idx: number) {
    const p = vs.prompts[idx]
    setEditingIdx(idx)
    setEditLabel(p.label)
    setEditSystem(p.system || '')
  }

  function cancelEdit() {
    setEditingIdx(null)
    setEditLabel('')
    setEditSystem('')
  }

  async function saveEdit() {
    if (editingIdx === null) return
    setSaving(true)
    const updated = vs.prompts.map((p, i) =>
      i === editingIdx ? { ...p, label: editLabel.trim(), system: editSystem.trim() } : p
    )
    try {
      const res = await fetch('/api/voice-scribe/prompts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompts: updated, whisper_prompt: vs.whisper_prompt }),
      })
      if (res.ok) {
        showToast('Prompt saved')
        setEditingIdx(null)
        fetchState()
      } else {
        const err = await res.json()
        showToast(`Error: ${err.error}`)
      }
    } finally { setSaving(false) }
  }

  async function deletePrompt(pad: number, label: string) {
    if (!confirm(`Delete "${label}"?`)) return
    const res = await fetch(`/api/voice-scribe/prompts/${pad}`, { method: 'DELETE' })
    if (res.ok) {
      showToast(`"${label}" deleted`)
      fetchState()
    }
  }

  async function addPrompt() {
    if (!newLabel.trim()) return
    setSaving(true)
    try {
      const res = await fetch('/api/voice-scribe/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: newLabel.trim(), system: newSystem.trim() }),
      })
      if (res.ok) {
        showToast('Prompt added')
        setAddingNew(false)
        setNewLabel('')
        setNewSystem('')
        fetchState()
      }
    } finally { setSaving(false) }
  }

  const isSpecial = (label: string) => SPECIAL_LABELS.includes(label)

  return (
    <div className="vs-root">
      {/* ── Header ── */}
      <div className="vs-header">
        <div className="vs-header-left">
          <span className="vs-title">Voice Scribe</span>
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

      {/* ── Prompt Pads ── */}
      <div className="vs-section-title">
        Prompts
        <button className="vs-add-btn" onClick={() => setAddingNew(true)} title="Add prompt">+</button>
      </div>

      {/* Add new prompt form */}
      {addingNew && (
        <div className="vs-prompt-edit-card">
          <input
            className="vs-edit-label"
            value={newLabel}
            onChange={e => setNewLabel(e.target.value)}
            placeholder="Label (e.g. Professional)"
            autoFocus
          />
          <textarea
            className="vs-edit-system"
            value={newSystem}
            onChange={e => setNewSystem(e.target.value)}
            placeholder="System prompt (leave empty for special labels like Cancel, Speak, etc.)"
            rows={3}
          />
          <div className="vs-edit-actions">
            <button className="vs-save-btn" onClick={addPrompt} disabled={saving || !newLabel.trim()}>
              Add
            </button>
            <button className="vs-cancel-btn" onClick={() => { setAddingNew(false); setNewLabel(''); setNewSystem('') }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="vs-prompt-grid">
        {vs.prompts.map((p, i) => (
          editingIdx === i ? (
            /* ── Edit mode ── */
            <div key={p.pad} className="vs-prompt-edit-card">
              <input
                className="vs-edit-label"
                value={editLabel}
                onChange={e => setEditLabel(e.target.value)}
                placeholder="Label"
                autoFocus
              />
              {!isSpecial(editLabel) && (
                <textarea
                  className="vs-edit-system"
                  value={editSystem}
                  onChange={e => setEditSystem(e.target.value)}
                  placeholder="System prompt"
                  rows={4}
                />
              )}
              {isSpecial(editLabel) && (
                <div className="vs-special-hint">Special prompt — no system text needed</div>
              )}
              <div className="vs-edit-actions">
                <button className="vs-save-btn" onClick={saveEdit} disabled={saving || !editLabel.trim()}>
                  Save
                </button>
                <button className="vs-cancel-btn" onClick={cancelEdit}>Cancel</button>
              </div>
            </div>
          ) : (
            /* ── View mode ── */
            <div key={p.pad} className={`vs-prompt-card ${isSpecial(p.label) ? 'vs-special' : ''}`}>
              <div className="vs-prompt-card-header">
                <span className="vs-prompt-card-pad">#{p.pad}</span>
                <span className="vs-prompt-card-name">{p.label}</span>
                {isSpecial(p.label) && <span className="vs-special-badge">special</span>}
                <div className="vs-prompt-card-btns">
                  <button className="vs-icon-btn" onClick={() => startEdit(i)} title="Edit">&#9998;</button>
                  <button className="vs-icon-btn vs-del-btn" onClick={() => deletePrompt(p.pad, p.label)} title="Delete">&times;</button>
                </div>
              </div>
              {p.system && (
                <div className="vs-prompt-card-desc">{p.system}</div>
              )}
            </div>
          )
        ))}
        {vs.prompts.length === 0 && (
          <div className="vs-empty-hint">No prompts — click + to add one</div>
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
