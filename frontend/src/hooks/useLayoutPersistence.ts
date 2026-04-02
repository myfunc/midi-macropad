/**
 * Save/load dockview layout to backend (settings.json) with debounce.
 * Falls back to localStorage if backend is unavailable.
 */

let saveTimer: number | null = null
const DEBOUNCE_MS = 1500

export async function loadLayout(): Promise<object | null> {
  // Try backend first
  try {
    const res = await fetch('/api/settings')
    if (res.ok) {
      const data = await res.json()
      const layout = data.values?.ui_layout
      if (layout && typeof layout === 'object') {
        return layout
      }
    }
  } catch {
    // Backend unavailable
  }

  // Fallback to localStorage
  try {
    const saved = localStorage.getItem('dockview-layout')
    if (saved) return JSON.parse(saved)
  } catch {
    // Corrupted
  }

  return null
}

export function saveLayout(layoutJson: object, isLeader: boolean): void {
  // Always save to localStorage (instant, for same-session recovery)
  try {
    localStorage.setItem('dockview-layout', JSON.stringify(layoutJson))
  } catch {
    // Quota exceeded
  }

  // Only leader saves to backend (debounced)
  if (!isLeader) return

  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    fetch('/api/settings/ui_layout', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: layoutJson }),
    }).catch(() => {
      // Backend unavailable — localStorage still has it
    })
  }, DEBOUNCE_MS)
}
