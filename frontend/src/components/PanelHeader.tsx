import { useAppStore } from '../stores/useAppStore'
import type { Panel, PanelBank } from '../types'

interface PanelHeaderProps {
  instanceId: string
  panel: Panel
}

export function PanelHeader({ instanceId, panel }: PanelHeaderProps) {
  const padPresets = useAppStore(s => s.presets)
  const knobPresets = useAppStore(s => s.knobPresets ?? [])
  const activePanels = useAppStore(s => s.activePanels)
  const updatePanel = useAppStore(s => s.updatePanelRequest)
  const activatePanel = useAppStore(s => s.activatePanelRequest)
  const deletePanel = useAppStore(s => s.deletePanelRequest)

  const sourcePresets = panel.type === 'pad' ? padPresets : knobPresets
  const presetNames = sourcePresets.map(p => p.name)
  const activeSlot = `${panel.type}:${panel.bank}` as 'pad:A' | 'pad:B' | 'knob:A' | 'knob:B'
  const isActive = activePanels[activeSlot] === instanceId

  function setBank(bank: PanelBank) {
    if (panel.bank === bank) return
    updatePanel(instanceId, { bank })
  }

  function setPreset(preset: string) {
    if (panel.preset === preset) return
    updatePanel(instanceId, { preset })
  }

  function onActivate() {
    if (isActive) return
    activatePanel(instanceId)
  }

  function onDelete() {
    if (!confirm(`Delete panel "${panel.title}"?`)) return
    deletePanel(instanceId)
  }

  return (
    <div className="panel-header">
      <div className="panel-header-banks">
        <button
          className={`panel-bank-btn${panel.bank === 'A' ? ' active' : ''}`}
          onClick={() => setBank('A')}
          title="Bank A"
        >A</button>
        <button
          className={`panel-bank-btn${panel.bank === 'B' ? ' active' : ''}`}
          onClick={() => setBank('B')}
          title="Bank B"
        >B</button>
      </div>
      <select
        className="panel-preset-select"
        value={panel.preset}
        onChange={e => setPreset(e.target.value)}
      >
        {presetNames.length === 0 && (
          <option value={panel.preset}>{panel.preset || '(no presets)'}</option>
        )}
        {presetNames.map(n => (
          <option key={n} value={n}>{n}</option>
        ))}
      </select>
      <button
        className={`panel-activate-btn${isActive ? ' is-active' : ''}`}
        onClick={onActivate}
        title={isActive ? 'Active — dispatching MIDI' : 'Click to activate'}
      >
        <span className="activate-led" />
        <span className="activate-text">
          {isActive ? 'ACTIVE' : 'INACTIVE'}
        </span>
      </button>
      <button
        className="panel-delete-btn"
        onClick={onDelete}
        title="Delete panel"
      >&times;</button>
    </div>
  )
}
