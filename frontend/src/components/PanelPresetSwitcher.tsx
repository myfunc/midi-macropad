import { useState, useRef, useEffect, useCallback } from 'react'
import { useAppStore } from '../stores/useAppStore'

interface PanelPresetSwitcherProps {
  panelId: string
}

const MAX_VISIBLE = 4

export function PanelPresetSwitcher({ panelId }: PanelPresetSwitcherProps) {
  const presets = useAppStore(s => s.presets)
  const panelPreset = useAppStore(s => s.panelPresets[panelId])
  const setPanelPreset = useAppStore(s => s.setPanelPreset)
  const updatePanelPresetName = useAppStore(s => s.updatePanelPresetName)
  const showToast = useAppStore(s => s.showToast)

  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; preset: string } | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [overflowOpen, setOverflowOpen] = useState(false)

  const inputRef = useRef<HTMLInputElement>(null)
  const renameRef = useRef<HTMLInputElement>(null)

  const activePreset = panelPreset?.preset ?? presets[0]?.name ?? ''
  const presetNames = presets.map(p => p.name)

  const needsOverflow = presetNames.length > MAX_VISIBLE
  const visiblePresets = needsOverflow ? presetNames.slice(0, MAX_VISIBLE) : presetNames
  const overflowPresets = needsOverflow ? presetNames.slice(MAX_VISIBLE) : []

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
    fetch(`/api/panels/${panelId}/preset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset: name }),
    }).then(r => {
      if (!r.ok) throw new Error(r.statusText)
    }).catch((e) => {
      console.error(`[PresetSwitcher] Failed to switch preset for ${panelId}:`, e)
      setPanelPreset(panelId, previousPreset)
    })
  }, [panelId, activePreset, setPanelPreset])

  function handleContextMenu(e: React.MouseEvent, preset: string) {
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY, preset })
  }

  async function createPreset() {
    const name = newName.trim()
    if (!name) return
    try {
      const res = await fetch('/api/presets', {
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
      const res = await fetch(`/api/presets/${encodeURIComponent(renaming)}`, {
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
      const res = await fetch(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' })
      if (res.ok) {
        showToast(`Preset "${name}" deleted`)
      }
    } catch {
      showToast('Network error')
    }
  }

  function renderPill(name: string) {
    if (renaming === name) {
      return (
        <input
          key={name}
          ref={renameRef}
          className="panel-preset-rename-input"
          value={renameValue}
          onChange={e => setRenameValue(e.target.value)}
          onBlur={renamePreset}
          onKeyDown={e => {
            if (e.key === 'Enter') renamePreset()
            if (e.key === 'Escape') setRenaming(null)
          }}
        />
      )
    }
    return (
      <button
        key={name}
        className={`panel-preset-pill ${name === activePreset ? 'active' : ''}`}
        onClick={() => switchPreset(name)}
        onContextMenu={e => handleContextMenu(e, name)}
      >
        {name}
      </button>
    )
  }

  return (
    <div className="panel-preset-switcher">
      {visiblePresets.map(renderPill)}

      {needsOverflow && (
        <div className="panel-preset-overflow-wrap">
          <button
            className={`panel-preset-pill overflow ${overflowPresets.includes(activePreset) ? 'active' : ''}`}
            onClick={() => setOverflowOpen(!overflowOpen)}
          >
            ...
          </button>
          {overflowOpen && (
            <div className="panel-preset-overflow-menu" onMouseLeave={() => setOverflowOpen(false)}>
              {overflowPresets.map(name => (
                <div
                  key={name}
                  className={`panel-preset-overflow-item ${name === activePreset ? 'active' : ''}`}
                  onClick={() => { switchPreset(name); setOverflowOpen(false) }}
                  onContextMenu={e => handleContextMenu(e, name)}
                >
                  {name}
                </div>
              ))}
            </div>
          )}
        </div>
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
