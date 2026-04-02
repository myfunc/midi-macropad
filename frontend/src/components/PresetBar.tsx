import { useAppStore } from '../stores/useAppStore'

export function PresetBar() {
  const presets = useAppStore(s => s.presets)
  const currentIndex = useAppStore(s => s.currentPresetIndex)

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
    </div>
  )
}
