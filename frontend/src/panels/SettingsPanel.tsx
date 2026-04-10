import { useState, useEffect } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

interface SettingsData {
  values: Record<string, unknown>
  profiles: string[]
  active_profile: string
}

export function SettingsPanel(_props: IDockviewPanelProps) {
  const midiConnected = useAppStore(s => s.midiConnected)
  const midiPortName = useAppStore(s => s.midiPortName)
  const plugins = useAppStore(s => s.plugins)
  const showToast = useAppStore(s => s.showToast)

  const [settings, setSettings] = useState<SettingsData | null>(null)
  const [profiles, setProfiles] = useState<string[]>([])
  const [activeProfile, setActiveProfile] = useState('')
  const [newProfileName, setNewProfileName] = useState('')

  useEffect(() => {
    fetch('/api/settings').then(r => r.json()).then(data => {
      setSettings(data)
      setProfiles(data.profiles || [])
      setActiveProfile(data.active_profile || 'default')
    }).catch(() => {})
  }, [])

  function loadProfile(name: string) {
    fetch(`/api/profiles/${name}/load`, { method: 'POST' })
      .then(r => r.json())
      .then(() => {
        setActiveProfile(name)
        showToast(`Profile loaded: ${name}`)
      })
  }

  function saveProfile(name?: string) {
    const n = name || activeProfile
    fetch(`/api/profiles/${n}/save`, { method: 'POST' })
      .then(() => {
        showToast(`Profile saved: ${n}`)
        if (name && !profiles.includes(name)) {
          setProfiles([...profiles, name].sort())
        }
      })
  }

  function saveSetting(key: string, value: unknown) {
    fetch(`/api/settings/${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }).then(() => {
      setSettings(prev => prev ? { ...prev, values: { ...prev.values, [key]: value } } : prev)
    })
  }

  function togglePlugin(name: string) {
    fetch(`/api/plugins/${name}/toggle`, { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        showToast(`${name}: ${data.enabled ? 'loaded' : 'unloaded'}`)
      })
  }

  const vals = settings?.values || {}

  return (
    <div className="settings-panel-content">
      {/* Profile Management */}
      <div className="settings-section">
        <div className="settings-section-title">Profile</div>
        <div className="settings-row">
          <span className="settings-label">Active</span>
          <select
            className="settings-select"
            value={activeProfile}
            onChange={(e) => loadProfile(e.target.value)}
          >
            {profiles.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
        <div className="settings-btn-row">
          <button className="settings-btn" onClick={() => saveProfile()}>Save</button>
          <button className="settings-btn" onClick={() => {
            const name = newProfileName.trim()
            if (name) { saveProfile(name); setNewProfileName('') }
          }}>Save As...</button>
          <input
            className="settings-input-sm"
            type="text"
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            placeholder="new name"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const name = newProfileName.trim()
                if (name) { saveProfile(name); setNewProfileName('') }
              }
            }}
          />
        </div>
      </div>

      {/* MIDI Device */}
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
        <button className="settings-btn" onClick={() => {
          fetch('/api/midi/reconnect', { method: 'POST' })
            .then(() => showToast('MIDI reconnect requested'))
        }}>
          Reconnect MIDI
        </button>
      </div>

      {/* General */}
      <div className="settings-section">
        <div className="settings-section-title">General</div>
        <div className="settings-row">
          <span className="settings-label">Feedback</span>
          <select
            className="settings-select"
            value={String(vals.feedback_mode || 'midi')}
            onChange={(e) => saveSetting('feedback_mode', e.target.value)}
          >
            <option value="midi">MIDI (keyboard)</option>
            <option value="audio">Audio (output)</option>
            <option value="both">Both</option>
            <option value="off">Off</option>
          </select>
        </div>
        <div className="settings-row">
          <span className="settings-label">Transpose</span>
          <input
            type="number"
            className="settings-input-num"
            value={Number(vals.melody_transpose || 0)}
            min={-24} max={24}
            onChange={(e) => saveSetting('melody_transpose', parseInt(e.target.value) || 0)}
          />
        </div>
        <div className="settings-row">
          <span className="settings-label">Master cap</span>
          <input
            type="range"
            className="settings-range"
            min={0} max={1} step={0.05}
            value={Number(vals.midi_master_cap ?? 1)}
            onChange={(e) => saveSetting('midi_master_cap', parseFloat(e.target.value))}
          />
          <span className="settings-value">{Math.round(Number(vals.midi_master_cap ?? 1) * 100)}%</span>
        </div>
        <div className="settings-row">
          <span className="settings-label">Mic cap</span>
          <input
            type="range"
            className="settings-range"
            min={0} max={1} step={0.05}
            value={Number(vals.midi_mic_cap ?? 1)}
            onChange={(e) => saveSetting('midi_mic_cap', parseFloat(e.target.value))}
          />
          <span className="settings-value">{Math.round(Number(vals.midi_mic_cap ?? 1) * 100)}%</span>
        </div>
      </div>

      {/* Plugins */}
      <div className="settings-section">
        <div className="settings-section-title">Plugins</div>
        {plugins.map(p => (
          <div key={p.name} className="settings-row">
            <span className="settings-label">{p.name}</span>
            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={p.enabled}
                onChange={() => togglePlugin(p.name)}
              />
              <span className="settings-toggle-label">
                {p.enabled ? 'ON' : 'OFF'}
              </span>
            </label>
          </div>
        ))}
      </div>

      {/* OBS Connection */}
      <div className="settings-section">
        <div className="settings-section-title">OBS Connection</div>
        <div className="settings-row">
          <span className="settings-label">Host</span>
          <input
            className="settings-input-sm"
            type="text"
            defaultValue={String((vals.obs_session_plugin as any)?.host || '127.0.0.1')}
            onBlur={(e) => {
              const obs = (vals.obs_session_plugin || {}) as Record<string, unknown>
              saveSetting('obs_session_plugin', { ...obs, host: e.target.value })
            }}
          />
        </div>
        <div className="settings-row">
          <span className="settings-label">Port</span>
          <input
            className="settings-input-sm"
            type="number"
            defaultValue={Number((vals.obs_session_plugin as any)?.port || 4455)}
            onBlur={(e) => {
              const obs = (vals.obs_session_plugin || {}) as Record<string, unknown>
              saveSetting('obs_session_plugin', { ...obs, port: parseInt(e.target.value) })
            }}
          />
        </div>
        <div className="settings-row">
          <span className="settings-label">Password</span>
          <input
            className="settings-input-sm"
            type="password"
            defaultValue={String((vals.obs_session_plugin as any)?.password || '')}
            onBlur={(e) => {
              const obs = (vals.obs_session_plugin || {}) as Record<string, unknown>
              saveSetting('obs_session_plugin', { ...obs, password: e.target.value })
            }}
          />
        </div>
      </div>
    </div>
  )
}
