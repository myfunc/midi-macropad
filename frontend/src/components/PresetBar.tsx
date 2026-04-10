import { useState, useCallback } from 'react'
import { useAppStore } from '../stores/useAppStore'

interface PresetBarProps {
  onOpenSettings: () => void
  onTogglePanel: (id: string, title: string) => void
  onResetLayout: () => void
  panels: readonly { id: string; title: string }[]
}

export function PresetBar({ onOpenSettings, onTogglePanel, onResetLayout, panels }: PresetBarProps) {
  const presets = useAppStore(s => s.presets)
  const currentIndex = useAppStore(s => s.currentPresetIndex)
  const [menuOpen, setMenuOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(!!document.fullscreenElement)

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      document.exitFullscreen().then(() => setIsFullscreen(false))
    } else {
      document.documentElement.requestFullscreen().then(() => setIsFullscreen(true))
    }
  }, [])

  function switchPreset(index: number) {
    fetch(`/api/presets/${index}/activate`, { method: 'POST' })
  }

  return (
    <div className="preset-bar">
      {presets.map(p => (
        <button
          key={p.index}
          className={`preset-chip ${p.index === currentIndex ? 'active' : ''}`}
          onClick={() => switchPreset(p.index)}
        >
          {p.name}
        </button>
      ))}
      <div className="preset-spacer" />

      {/* View menu */}
      <div style={{ position: 'relative' }}>
        <button
          className="toolbar-btn"
          onClick={() => setMenuOpen(!menuOpen)}
          title="Panels"
        >
          &#9783;
        </button>
        {menuOpen && (
          <div className="panel-menu" onMouseLeave={() => setMenuOpen(false)}>
            {panels.map(p => (
              <div
                key={p.id}
                className="panel-menu-item"
                onClick={() => { onTogglePanel(p.id, p.title); setMenuOpen(false) }}
              >
                {p.title}
              </div>
            ))}
            <div className="panel-menu-divider" />
            <div
              className="panel-menu-item reset"
              onClick={() => { onResetLayout(); setMenuOpen(false) }}
            >
              Reset Layout
            </div>
          </div>
        )}
      </div>

      {/* Fullscreen */}
      <button className="toolbar-btn" onClick={toggleFullscreen} title="Fullscreen">
        {isFullscreen ? '\u2716' : '\u26F6'}
      </button>

      {/* Settings gear */}
      <button className="toolbar-btn" onClick={onOpenSettings} title="Settings">
        &#9881;
      </button>
    </div>
  )
}
