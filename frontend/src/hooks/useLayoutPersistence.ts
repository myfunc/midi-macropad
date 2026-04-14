/**
 * Save/load dockview layout to backend (settings.json) with debounce.
 * Falls back to localStorage if backend is unavailable.
 */

let saveTimer: number | null = null
const DEBOUNCE_MS = 1500

// Bump when panel ids change in a non-backward-compatible way so stale saved
// layouts get discarded and the default layout rebuilds.
// v3: added piano panels (bank play/map) + stricter orphan filtering.
const LAYOUT_SCHEMA_VERSION = 3

// Utility panels that are allowed in a layout even if they're not tracked in
// the freeform panels store.
const UTILITY_PANEL_IDS = new Set(['properties', 'log', 'settings'])

function isLegacyLayout(layout: unknown): boolean {
  if (!layout || typeof layout !== 'object') return true
  const anyLayout = layout as { schemaVersion?: number }
  // If the layout has NO schemaVersion field it was saved before we started
  // stamping them, so treat it as stale and force a rebuild.
  if (typeof anyLayout.schemaVersion !== 'number') return true
  if (anyLayout.schemaVersion !== LAYOUT_SCHEMA_VERSION) return true
  const serialized = JSON.stringify(layout)
  // v1 used a single 'padgrid' panel; v2 splits into bankA/bankB/knobs.
  return serialized.includes('"padgrid"')
}

/** Strip bookkeeping fields that dockview.fromJSON would reject. */
function stripSchemaMeta(layout: object): object {
  const clone = JSON.parse(JSON.stringify(layout)) as Record<string, unknown>
  delete clone.schemaVersion
  return clone
}

/**
 * Remove orphan panel-ids from a saved layout JSON — ids that are neither
 * tracked in the freeform panels dict nor in the utility-panel allowlist.
 *
 * Works in-place on a cloned copy and returns it. If nothing was filtered,
 * returns the original reference.
 */
export function filterOrphanPanelRefs(
  layout: unknown,
  knownPanelIds: Iterable<string>,
): unknown {
  if (!layout || typeof layout !== 'object') return layout
  const allowed = new Set<string>(UTILITY_PANEL_IDS)
  for (const id of knownPanelIds) allowed.add(id)

  // Dockview layout shape: { grid: {...}, panels: { [id]: {...} }, ... }
  const clone = JSON.parse(JSON.stringify(layout))
  const anyLayout = clone as { panels?: Record<string, unknown> }
  if (anyLayout.panels && typeof anyLayout.panels === 'object') {
    const removed: string[] = []
    for (const pid of Object.keys(anyLayout.panels)) {
      if (!allowed.has(pid)) {
        delete anyLayout.panels[pid]
        removed.push(pid)
      }
    }
    if (removed.length > 0) {
      console.warn('[Layout] Filtered orphan panel refs:', removed)
    }
  }
  return clone
}

export async function loadLayout(
  knownPanelIds: Iterable<string> = [],
): Promise<object | null> {
  // Try backend first
  let layout: unknown = null
  try {
    const res = await fetch('/api/settings')
    if (res.ok) {
      const data = await res.json()
      const backendLayout = data.values?.ui_layout
      if (backendLayout && typeof backendLayout === 'object' && !isLegacyLayout(backendLayout)) {
        layout = backendLayout
      }
    }
  } catch {
    // Backend unavailable
  }

  if (!layout) {
    // Fallback to localStorage
    try {
      const saved = localStorage.getItem('dockview-layout')
      if (saved) {
        const parsed = JSON.parse(saved)
        if (!isLegacyLayout(parsed)) layout = parsed
        else localStorage.removeItem('dockview-layout')
      }
    } catch {
      // Corrupted
    }
  }

  if (!layout) return null
  const filtered = filterOrphanPanelRefs(layout, knownPanelIds) as object
  // Dockview's fromJSON tolerates extra top-level fields on most versions,
  // but strip our internal schemaVersion marker defensively to avoid
  // triggering validator warnings in future versions.
  return stripSchemaMeta(filtered)
}

export { LAYOUT_SCHEMA_VERSION }

export function saveLayout(layoutJson: object, isLeader: boolean): void {
  // Stamp the schema version so stale layouts get invalidated on future
  // breaking-panel-id changes (see LAYOUT_SCHEMA_VERSION bumps).
  const wrapped = { ...layoutJson, schemaVersion: LAYOUT_SCHEMA_VERSION }

  // Always save to localStorage (instant, for same-session recovery)
  try {
    localStorage.setItem('dockview-layout', JSON.stringify(wrapped))
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
      body: JSON.stringify({ value: wrapped }),
    }).catch(() => {
      // Backend unavailable — localStorage still has it
    })
  }, DEBOUNCE_MS)
}
