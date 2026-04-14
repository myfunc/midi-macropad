import { useAppStore } from '../stores/useAppStore'
import type { ActivePanelKey, Panel, PanelBank } from '../types'

interface PanelHeaderProps {
  instanceId: string
  panel: Panel
}

// Regex matching canonical template titles. When the current title matches
// this pattern, changing bank auto-rewrites the title to the new template;
// custom titles are preserved as-is.
const TEMPLATE_TITLE_RE =
  /^(?:Pad Panel [AB]|Knob Panel [AB]|Piano \((?:Play|Map)\))$/

function templateTitle(type: Panel['type'], bank: PanelBank): string {
  if (type === 'pad') return `Pad Panel ${bank}`
  if (type === 'knob') return `Knob Panel ${bank}`
  // piano
  return bank === 'map' ? 'Piano (Map)' : 'Piano (Play)'
}

export function PanelHeader({ instanceId, panel }: PanelHeaderProps) {
  const padPresets = useAppStore(s => s.presets)
  const knobPresets = useAppStore(s => s.knobPresets ?? [])
  const pianoPresets = useAppStore(s => s.pianoPresets ?? [])
  const activePanels = useAppStore(s => s.activePanels)
  const updatePanel = useAppStore(s => s.updatePanelRequest)
  const activatePanel = useAppStore(s => s.activatePanelRequest)
  const deletePanel = useAppStore(s => s.deletePanelRequest)

  const sourcePresets =
    panel.type === 'pad' ? padPresets :
    panel.type === 'knob' ? knobPresets :
    pianoPresets
  const presetNames = sourcePresets.map(p => p.name)
  const activeSlot = `${panel.type}:${panel.bank}` as ActivePanelKey
  const isActive = activePanels[activeSlot] === instanceId

  // Bank options vary by panel type.
  const bankOptions: { value: PanelBank; label: string }[] =
    panel.type === 'piano'
      ? [
          { value: 'play', label: 'Play' },
          { value: 'map', label: 'Map' },
        ]
      : [
          { value: 'A', label: 'A' },
          { value: 'B', label: 'B' },
        ]

  function setBank(bank: PanelBank) {
    if (panel.bank === bank) return
    // Title auto-sync: if current title matches the template pattern, rewrite
    // it to match the new bank. Custom titles are preserved.
    const patch: Partial<Pick<Panel, 'bank' | 'title' | 'preset'>> = { bank }
    if (TEMPLATE_TITLE_RE.test(panel.title)) {
      patch.title = templateTitle(panel.type, bank)
    }
    updatePanel(instanceId, patch)
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
        {bankOptions.map(opt => (
          <button
            key={opt.value}
            className={`panel-bank-btn${panel.bank === opt.value ? ' active' : ''}`}
            onClick={() => setBank(opt.value)}
            title={`Bank ${opt.label}`}
          >{opt.label}</button>
        ))}
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
