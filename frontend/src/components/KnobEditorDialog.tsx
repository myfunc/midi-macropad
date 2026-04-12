import { useState, useEffect } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { CatalogAction } from '../types'

interface ActionGroup {
  label: string
  actions: CatalogAction[]
}

/**
 * Inline knob properties view — renders inside PropertiesPanel,
 * not as a modal overlay.
 */
export function KnobPropertiesView({ cc }: { cc: number }) {
  const knob = useAppStore(s => s.knobs.find(k => k.cc === cc))
  const liveValue = useAppStore(s => s.knobs.find(k => k.cc === cc)?.value ?? 0)
  const catalog = useAppStore(s => s.knobCatalog)
  const fetchKnobCatalog = useAppStore(s => s.fetchKnobCatalog)
  const showToast = useAppStore(s => s.showToast)

  const [label, setLabel] = useState(knob?.label ?? '')
  const [selectedActionId, setSelectedActionId] = useState(
    knob ? `${knob.action.type}:${knob.action.target}` : ''
  )
  const [params, setParams] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => { fetchKnobCatalog() }, [fetchKnobCatalog])

  useEffect(() => {
    if (knob) {
      setLabel(knob.label)
      setSelectedActionId(`${knob.action.type}:${knob.action.target}`)
    }
  }, [knob?.label, knob?.action.type, knob?.action.target])

  if (!knob) return null

  const pct = Math.round((liveValue / 127) * 100)
  const angle = (liveValue / 127) * 270 - 135

  const actionGroups: ActionGroup[] = []
  if (catalog) {
    if (catalog.core.length > 0) {
      actionGroups.push({ label: 'System', actions: catalog.core })
    }
    for (const [pluginName, actions] of Object.entries(catalog.plugins)) {
      if (actions.length > 0) {
        actionGroups.push({ label: pluginName, actions })
      }
    }
  }

  const allActions: CatalogAction[] = catalog
    ? [...catalog.core, ...Object.values(catalog.plugins).flat()]
    : []
  const actionKey = (a: CatalogAction) => `${a.type}:${a.target}`
  const selectedAction = allActions.find(a => actionKey(a) === selectedActionId)

  const paramsSchema = selectedAction?.params_schema ?? {}
  const paramKeys = Object.keys(paramsSchema)

  async function handleSave() {
    if (!selectedAction) return
    setSaving(true)
    try {
      const body: Record<string, unknown> = {
        type: selectedAction.type,
        target: selectedAction.target,
        label: label.trim() || selectedAction.label,
      }
      if (paramKeys.length > 0) {
        body.params = params
      }
      const res = await fetch(`/api/knobs/${cc}/action`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        showToast(`Knob CC${cc} saved`)
      } else {
        const err = await res.json().catch(() => ({ error: 'Unknown error' }))
        showToast(`Error: ${err.error || res.statusText}`)
      }
    } catch (e) {
      showToast(`Network error: ${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="properties-content">
      <div className="props-header">
        <span className="pad-badge" style={{ background: 'var(--accent)' }}>Knob</span>
        <span className="props-note">CC {cc}</span>
      </div>
      <div className="props-current">
        Current: <strong>{knob.label}</strong>
      </div>

      <div className="knob-editor-value-display">
        <div className="knob-visual">
          <div className="knob-needle" style={{ transform: `rotate(${angle}deg)` }} />
        </div>
        <div className="value-text">
          <span className="val-big">{liveValue}</span>
          <span className="val-sub">{pct}% (0-127)</span>
        </div>
      </div>

      <div className="props-section">
        <label className="props-label">Label</label>
        <input
          className="props-input"
          type="text"
          value={label}
          onChange={e => setLabel(e.target.value)}
        />
      </div>

      <div className="props-section">
        <label className="props-label">Action</label>
        <select
          className="props-input"
          value={selectedActionId}
          onChange={e => {
            setSelectedActionId(e.target.value)
            setParams({})
          }}
        >
          <option value="">-- Select --</option>
          {actionGroups.map(group => (
            <optgroup key={group.label} label={group.label}>
              {group.actions.map(a => (
                <option key={actionKey(a)} value={actionKey(a)}>{a.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {selectedAction?.description && (
        <div className="props-section">
          <label className="props-label">Info</label>
          <span className="props-value-hint">{selectedAction.description}</span>
        </div>
      )}

      {paramKeys.length > 0 && (
        <div className="props-section">
          <div className="props-section-title">Parameters</div>
          {paramKeys.map(key => (
            <div key={key} className="props-section">
              <label className="props-label">{key}</label>
              <input
                className="props-input"
                type="text"
                value={params[key] ?? ''}
                onChange={e => setParams(prev => ({ ...prev, [key]: e.target.value }))}
                placeholder={String((paramsSchema as Record<string, unknown>)[key] ?? '')}
              />
            </div>
          ))}
        </div>
      )}

      <button
        className="btn-save"
        onClick={handleSave}
        disabled={saving || !selectedActionId}
        style={{ marginTop: 12, width: '100%' }}
      >
        {saving ? 'Saving...' : 'Save'}
      </button>
    </div>
  )
}
