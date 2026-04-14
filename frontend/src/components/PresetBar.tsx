import { useCallback, useRef, useState } from 'react'
import type { MenuAction, MenuGroup } from '../App'

interface PresetBarProps {
  onMenuAction: (action: MenuAction) => void
  menu: readonly MenuGroup[]
}

export function PresetBar({ onMenuAction, menu }: PresetBarProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [hoveredGroup, setHoveredGroup] = useState<string | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(!!document.fullscreenElement)

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      document.exitFullscreen().then(() => setIsFullscreen(false))
    } else {
      document.documentElement.requestFullscreen().then(() => setIsFullscreen(true))
    }
  }, [])

  const closeTimerRef = useRef<number | null>(null)

  function closeMenu() {
    setMenuOpen(false)
    setHoveredGroup(null)
  }

  function scheduleClose() {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current)
    closeTimerRef.current = window.setTimeout(() => closeMenu(), 150)
  }

  function cancelClose() {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }

  function fireAction(action: MenuAction) {
    cancelClose()
    onMenuAction(action)
    closeMenu()
  }

  return (
    <div className="preset-bar">
      <div className="preset-spacer" />

      {/* Menu */}
      <div style={{ position: 'relative' }}>
        <button
          className="toolbar-btn"
          onClick={() => setMenuOpen(!menuOpen)}
          title="Menu"
        >
          &#9776;
        </button>
        {menuOpen && (
          <div
            className="panel-menu"
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            {menu.map(group => {
              const isHovered = hoveredGroup === group.label
              return (
                <div
                  key={group.label}
                  className={`panel-menu-item panel-menu-parent${isHovered ? ' active' : ''}`}
                  onMouseEnter={() => { cancelClose(); setHoveredGroup(group.label) }}
                >
                  <span>{group.label}</span>
                  <span className="panel-menu-chevron">&#9656;</span>
                  {isHovered && (
                    <div
                      className="panel-submenu"
                      onMouseEnter={cancelClose}
                      onMouseLeave={scheduleClose}
                    >
                      {group.items.map(item => (
                        <div
                          key={item.label}
                          className="panel-menu-item"
                          onClick={() => fireAction(item.action)}
                        >
                          {item.label}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
            <div className="panel-menu-divider" />
            <div
              className="panel-menu-item reset"
              onClick={() => fireAction({ kind: 'resetLayout' })}
            >
              Reset Layout
            </div>
          </div>
        )}
      </div>

      <button className="toolbar-btn" onClick={toggleFullscreen} title="Fullscreen">
        {isFullscreen ? '\u2716' : '\u26F6'}
      </button>
    </div>
  )
}
