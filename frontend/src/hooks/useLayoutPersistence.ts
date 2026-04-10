/**
 * Save/load dockview layout to backend (settings.json) with debounce.
 * Falls back to localStorage if backend is unavailable.
 */

let saveTimer: number | null = null
const DEBOUNCE_MS = 1500

// Bump when panel ids change in a non-backward-compatible way so stale saved
// layouts get discarded and the default layout rebuilds.
const LAYOUT_SCHEMA_VERSION = 2

function isLegacyLayout(layout: unknown): boolean {
  if (!layout || typeof layout !== 'object') return true
  const serialized = JSON.stringify(layout)
  // v1 used a single 'padgrid' panel; v2 splits into bankA/bankB/knobs.
  return serialized.includes('"padgrid"')
}

export async function loadLayout(): Promise<object | null> {
  // Try backend first
  try {
    const res = await fetch('/api/settings')
    if (res.ok) {
      const data = await res.json()
      const layout = data.values?.ui_layout
      if (layout && typeof layout === 'object' && !isLegacyLayout(layout)) {
        return layout
      }
    }
  } catch {
    // Backend unavailable
  }

  // Fallback to localStorage
  try {
    const saved = localStorage.getItem('dockview-layout')
    if (saved) {
      const parsed = JSON.parse(saved)
      if (!isLegacyLayout(parsed)) return parsed
      localStorage.removeItem('dockview-layout')
    }
  } catch {
    // Corrupted
  }

  return null
}

export { LAYOUT_SCHEMA_VERSION }

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
