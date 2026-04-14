import { useState, useRef, useEffect, useCallback } from 'react'
import { useAppStore } from '../stores/useAppStore'

interface PanelPresetSwitcherProps {
  panelId: string
}

export function PanelPresetSwitcher({ panelId }: PanelPresetSwitcherProps) {
  const padPresets = useAppStore(s => s.presets)
  const knobPresets = useAppStore(s => s.knobPresets ?? [])
  const panelPreset = useAppStore(s => s.panelPresets[panelId])
  const setPanelPreset = useAppStore(s => s.setPanelPreset)
  const updatePanelPresetName = useAppStore(s => s.updatePanelPresetName)
  const showToast = useAppStore(s => s.showToast)

  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; preset: string } | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  const inputRef = useRef<HTMLInputElement>(null)
  const renameRef = useRef<HTMLInputElement>(null)

  // Knobs panel uses knob presets, pad panels use pad presets
  const isKnobs = panelId === 'knobs' || panelId.startsWith('knob-') || panelId.startsWith('knobBank')
  const sourcePresets = isKnobs ? knobPresets : padPresets
  const activePreset = panelPreset?.preset ?? sourcePresets[0]?.name ?? ''
  const presetNames = sourcePresets.map(p => p.name)

  useEffect(() => {
    if (creating) inputRef.current?.focus()
  }, [creating])

  useEffect(() => {
    if (renaming) renameRef.current?.focus()
  }, [renaming])

  // Close context menu on outside click
  useEffect(() => {
    if (!contextMenu) return
    const handler = () => setContextMenu(null)
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [contextMenu])

  const switchPreset = useCallback((name: string) => {
    const previousPreset = activePreset
    setPanelPreset(panelId, name)
    const url = isKnobs
      ? '/api/knob-presets/activate'
      : `/api/panels/${panelId}/preset`
    // Include bank from panel state so backend can route MIDI correctly
    const panelState = useAppStore.getState().panelPresets[panelId]
    const bank = panelState?.bank
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, preset: name, ...(bank ? { bank } : {}) }),
    }).then(r => {
      if (!r.ok) throw new Error(r.statusText)
    }).catch((e) => {
      console.error(`[PresetSwitcher] Failed to switch preset for ${panelId}:`, e)
      setPanelPreset(panelId, previousPreset)
    })
  }, [panelId, activePreset, setPanelPreset, isKnobs])

  function handleContextMenu(e: React.MouseEvent) {
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY, preset: activePreset })
  }

  async function createPreset() {
    const name = newName.trim()
    if (!name) return
    try {
      const url = isKnobs ? '/api/knob-presets' : '/api/presets'
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (res.ok) {
        showToast(`Preset "${name}" created`)
        setCreating(false)
        setNewName('')
      } else {
        const err = await res.json().catch(() => ({}))
        showToast(`Error: ${(err as Record<string, string>).error || res.statusText}`)
      }
    } catch {
      showToast('Network error')
    }
  }

  async function renamePreset() {
    if (!renaming) return
    const name = renameValue.trim()
    if (!name || name === renaming) { setRenaming(null); return }
    try {
      const base = isKnobs ? '/api/knob-presets' : '/api/presets'
      const res = await fetch(`${base}/${encodeURIComponent(renaming)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (res.ok) {
        showToast(`Renamed to "${name}"`)
        // Update all panels that reference the old name
        updatePanelPresetName(renaming, name)
      } else {
        const err = await res.json().catch(() => ({}))
        showToast(`Error: ${(err as Record<string, string>).error || res.statusText}`)
      }
    } catch {
      showToast('Network error')
    }
    setRenaming(null)
  }

  async function deletePreset(name: string) {
    if (!confirm(`Delete preset "${name}"?`)) return
    try {
      const base = isKnobs ? '/api/knob-presets' : '/api/presets'
      const res = await fetch(`${base}/${encodeURIComponent(name)}`, { method: 'DELETE' })
      if (res.ok) {
        showToast(`Preset "${name}" deleted`)
      }
    } catch {
      showToast('Network error')
    }
  }

  return (
    <div className="panel-preset-switcher">
      {renaming ? (
        <input
          ref={renameRef}
          className="panel-preset-rename-input"
          value={renameValue}
          onChange={e => setRenameValue(e.target.value)}
          onBlur={renamePreset}
          onKeyDown={e => {
            if (e.key === 'Enter') renamePreset()
            if (e.key === 'Escape') setRenaming(null)
          }}
          style={{ flex: 1, minWidth: 0 }}
        />
      ) : (
        <select
          value={activePreset}
          onChange={e => switchPreset(e.target.value)}
          onContextMenu={handleContextMenu}
          style={{
            flex: 1,
            minWidth: 0,
            background: '#1c2333',
            color: '#c9d1d9',
            border: '1px solid #30363d',
            borderRadius: 4,
            padding: '3px 8px',
            fontSize: 12,
            outline: 'none',
            cursor: 'pointer',
          }}
          onFocus={e => { e.currentTarget.style.borderColor = '#58a6ff' }}
          onBlur={e => { e.currentTarget.style.borderColor = '#30363d' }}
        >
          {presetNames.map(name => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      )}

      {creating ? (
        <input
          ref={inputRef}
          className="panel-preset-new-input"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onBlur={() => { if (!newName.trim()) setCreating(false) }}
          onKeyDown={e => {
            if (e.key === 'Enter') createPreset()
            if (e.key === 'Escape') { setCreating(false); setNewName('') }
          }}
          placeholder="name..."
        />
      ) : (
        <button
          className="panel-preset-pill add"
          onClick={() => setCreating(true)}
          title="New preset"
        >
          +
        </button>
      )}

      {contextMenu && (
        <div
          className="panel-preset-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <div
            className="panel-preset-context-item"
            onClick={(e) => {
              e.stopPropagation()
              setRenaming(contextMenu.preset)
              setRenameValue(contextMenu.preset)
              setContextMenu(null)
            }}
          >
            Rename
          </div>
          <div
            className="panel-preset-context-item danger"
            onClick={(e) => {
              e.stopPropagation()
              deletePreset(contextMenu.preset)
              setContextMenu(null)
            }}
          >
            Delete
          </div>
        </div>
      )}
    </div>
  )
}
