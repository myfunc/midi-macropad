import { create } from 'zustand'
import type { PadEntry, KnobEntry, PresetInfo, PluginInfo, LogEntry, ObsState, KnobCatalog, PanelPresetState, Panel, ActivePanelsMap, PanelType, PanelBank } from '../types'

interface AppStore {
  // MIDI
  midiConnected: boolean
  midiPortName: string | null
  midiDeviceName: string

  // Presets
  presets: PresetInfo[]
  knobPresets: PresetInfo[]
  currentPresetIndex: number

  // Pads — composite keys "Preset:note"
  pads: Record<string, PadEntry>
  selectedPadKey: string | null

  // Knobs
  knobs: KnobEntry[]
  selectedKnobCC: number | null

  // Plugins
  plugins: PluginInfo[]

  // OBS
  obs: ObsState

  // Logs
  logs: LogEntry[]

  // Flash state (which pad keys are currently pressed)
  flashedPads: Set<string>

  // Toast
  toastMessage: string | null

  // Panel presets (per-panel independent preset + order)
  panelPresets: Record<string, PanelPresetState>

  // Active MIDI presets routing (panel_id -> preset_name)
  activeMidiPresets: Record<string, string>

  // Active knob presets routing (knobBank-A/B -> preset_name)
  activeKnobPresets: Record<string, string>

  // Knob catalog (cached)
  knobCatalog: KnobCatalog | null
  knobCatalogLoading: boolean

  // Freeform panels
  panels: Record<string, Panel>
  activePanels: ActivePanelsMap

  // Piano state (for future PianoPanel)
  pianoKeysPressed: Set<number>

  // Actions
  setInitialState: (state: Record<string, unknown>) => void
  selectPad: (key: string | null) => void
  selectKnob: (cc: number | null) => void
  flashPad: (key: string) => void
  releasePad: (key: string) => void
  updateKnob: (cc: number, value: number) => void
  addLog: (entry: LogEntry) => void
  setPresetIndex: (index: number) => void
  updatePads: (pads: Record<string, PadEntry>) => void
  showToast: (message: string) => void
  clearToast: () => void
  updateObs: (partial: Partial<ObsState>) => void
  setPanelPreset: (panelId: string, presetName: string) => void
  setPanelOrder: (panelId: string, order: number[]) => void
  setKnobCatalog: (catalog: KnobCatalog) => void
  fetchKnobCatalog: () => void
  setPresets: (presets: PresetInfo[]) => void
  setPanelPresets: (panelPresets: Record<string, PanelPresetState>) => void
  fetchPresets: () => void
  updateKnobLabel: (cc: number, label: string) => void
  updatePanelPresetName: (oldName: string, newName: string) => void
  setActiveMidiPresets: (routing: Record<string, string>) => void
  setActiveKnobPresets: (routing: Record<string, string>) => void
  setPanelBank: (panelId: string, bank: string) => void
  setPianoKey: (note: number, pressed: boolean) => void

  // Freeform panel actions
  setPanels: (panels: Record<string, Panel>) => void
  setActivePanels: (active: ActivePanelsMap) => void
  upsertPanel: (panel: Panel) => void
  removePanel: (instanceId: string) => void
  createPanelRequest: (type: PanelType, bank?: PanelBank) => Promise<Panel | null>
  updatePanelRequest: (id: string, patch: Partial<Pick<Panel, 'bank' | 'preset' | 'title'>>) => Promise<void>
  activatePanelRequest: (id: string) => Promise<void>
  deletePanelRequest: (id: string) => Promise<void>
}

const MAX_LOGS = 500

export const useAppStore = create<AppStore>((set) => ({
  midiConnected: false,
  midiPortName: null,
  midiDeviceName: '',
  presets: [],
  knobPresets: [],
  currentPresetIndex: 0,
  pads: {},
  selectedPadKey: null,
  knobs: [],
  selectedKnobCC: null,
  plugins: [],
  obs: {
    connected: false,
    current_scene: '',
    is_recording: false,
    is_streaming: false,
    is_replay_buffer_active: false,
    scenes: [],
  },
  logs: [],
  flashedPads: new Set(),
  toastMessage: null,
  panelPresets: {},
  activeMidiPresets: {},
  activeKnobPresets: {},
  knobCatalog: null,
  knobCatalogLoading: false,
  panels: {},
  activePanels: {},
  pianoKeysPressed: new Set(),

  setInitialState: (state) => set(() => {
    const s = state as Record<string, any>
    return {
      midiConnected: s.midi?.connected ?? false,
      midiPortName: s.midi?.port_name ?? null,
      midiDeviceName: s.midi?.device_name ?? '',
      presets: s.presets?.list ?? [],
      knobPresets: s.knob_presets ?? [],
      currentPresetIndex: s.presets?.current_index ?? 0,
      pads: s.pads ?? {},
      knobs: s.knobs ?? [],
      plugins: s.plugins?.discovered ?? [],
      obs: s.obs ?? {},
      logs: s.logs ?? [],
      panelPresets: s.panel_presets ?? {},
      activeMidiPresets: s.active_midi_presets ?? {},
      activeKnobPresets: s.active_knob_presets ?? {},
      panels: s.panels ?? {},
      activePanels: s.active_panels ?? {},
    }
  }),

  selectPad: (key) => set({ selectedPadKey: key, selectedKnobCC: null }),
  selectKnob: (cc) => set({ selectedKnobCC: cc, selectedPadKey: null }),

  flashPad: (key) => set((s) => {
    const next = new Set(s.flashedPads)
    next.add(key)
    return { flashedPads: next }
  }),

  releasePad: (key) => set((s) => {
    const next = new Set(s.flashedPads)
    next.delete(key)
    return { flashedPads: next }
  }),

  updateKnob: (cc, value) => set((s) => ({
    knobs: s.knobs.map(k => k.cc === cc ? { ...k, value } : k),
  })),

  addLog: (entry) => set((s) => ({
    logs: [...s.logs.slice(-(MAX_LOGS - 1)), entry],
  })),

  setPresetIndex: (index) => set({ currentPresetIndex: index }),

  updatePads: (pads) => set({ pads }),

  showToast: (message) => set({ toastMessage: message }),
  clearToast: () => set({ toastMessage: null }),

  updateKnobLabel: (cc, label) => set((s) => ({
    knobs: s.knobs.map(k => k.cc === cc ? { ...k, label } : k),
  })),

  updateObs: (partial) => set((s) => ({
    obs: { ...s.obs, ...partial },
  })),

  setPanelPreset: (panelId, presetName) => set((s) => ({
    panelPresets: {
      ...s.panelPresets,
      [panelId]: { ...s.panelPresets[panelId], preset: presetName, order: s.panelPresets[panelId]?.order ?? [] },
    },
  })),

  setPanelOrder: (panelId, order) => set((s) => ({
    panelPresets: {
      ...s.panelPresets,
      [panelId]: { ...s.panelPresets[panelId], preset: s.panelPresets[panelId]?.preset ?? '', order },
    },
  })),

  setPanelBank: (panelId: string, bank: string) => set((s) => ({
    panelPresets: {
      ...s.panelPresets,
      [panelId]: { ...s.panelPresets[panelId], preset: s.panelPresets[panelId]?.preset ?? '', order: s.panelPresets[panelId]?.order ?? [], bank },
    },
  })),

  setKnobCatalog: (catalog) => set({ knobCatalog: catalog, knobCatalogLoading: false }),

  fetchKnobCatalog: () => {
    const state = useAppStore.getState()
    if (state.knobCatalog || state.knobCatalogLoading) return
    set({ knobCatalogLoading: true })
    fetch('/api/knobs/catalog')
      .then(r => {
        if (!r.ok) throw new Error(r.statusText)
        return r.json()
      })
      .then(catalog => set({ knobCatalog: catalog, knobCatalogLoading: false }))
      .catch((e) => {
        console.error('[Store] Failed to fetch knob catalog:', e)
        set({ knobCatalogLoading: false })
      })
  },

  setPresets: (presets) => set({ presets }),

  setPanelPresets: (panelPresets) => set({ panelPresets }),

  fetchPresets: () => {
    fetch('/api/presets')
      .then(r => {
        if (!r.ok) throw new Error(r.statusText)
        return r.json()
      })
      .then(data => {
        const list = data.list ?? data.presets ?? data
        if (Array.isArray(list)) {
          set({ presets: list })
        }
      })
      .catch(e => console.error('[Store] Failed to fetch presets:', e))
  },

  updatePanelPresetName: (oldName, newName) => set((s) => {
    const updated: Record<string, PanelPresetState> = {}
    let changed = false
    for (const [panelId, ps] of Object.entries(s.panelPresets)) {
      if (ps.preset === oldName) {
        updated[panelId] = { ...ps, preset: newName }
        changed = true
      } else {
        updated[panelId] = ps
      }
    }
    return changed ? { panelPresets: updated } : {}
  }),

  setActiveMidiPresets: (routing) => set({ activeMidiPresets: routing }),
  setActiveKnobPresets: (routing) => set({ activeKnobPresets: routing }),

  setPianoKey: (note, pressed) => set((s) => {
    const next = new Set(s.pianoKeysPressed)
    if (pressed) next.add(note); else next.delete(note)
    return { pianoKeysPressed: next }
  }),

  setPanels: (panels) => set({ panels }),
  setActivePanels: (active) => set({ activePanels: active }),

  upsertPanel: (panel) => set((s) => ({
    panels: { ...s.panels, [panel.instanceId]: panel },
  })),

  removePanel: (instanceId) => set((s) => {
    const next = { ...s.panels }
    delete next[instanceId]
    const active: ActivePanelsMap = { ...s.activePanels }
    for (const k of Object.keys(active) as (keyof ActivePanelsMap)[]) {
      if (active[k] === instanceId) active[k] = null
    }
    return { panels: next, activePanels: active }
  }),

  createPanelRequest: async (type, bank = 'A') => {
    try {
      const res = await fetch('/api/panels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, bank }),
      })
      if (!res.ok) throw new Error(res.statusText)
      const panel: Panel = await res.json()
      useAppStore.getState().upsertPanel(panel)
      return panel
    } catch (e) {
      console.error('[Store] createPanel failed:', e)
      return null
    }
  },

  updatePanelRequest: async (id, patch) => {
    // Optimistic
    const prev = useAppStore.getState().panels[id]
    if (prev) useAppStore.getState().upsertPanel({ ...prev, ...patch })
    try {
      const res = await fetch(`/api/panels/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) throw new Error(res.statusText)
      const panel: Panel = await res.json()
      useAppStore.getState().upsertPanel(panel)
    } catch (e) {
      console.error('[Store] updatePanel failed:', e)
      if (prev) useAppStore.getState().upsertPanel(prev)
    }
  },

  activatePanelRequest: async (id) => {
    // Optimistic: clear other on same (type, bank)
    const panel = useAppStore.getState().panels[id]
    if (panel) {
      const key = `${panel.type}:${panel.bank}` as keyof ActivePanelsMap
      set((s) => ({ activePanels: { ...s.activePanels, [key]: id } }))
    }
    try {
      const res = await fetch(`/api/panels/${encodeURIComponent(id)}/activate`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error(res.statusText)
    } catch (e) {
      console.error('[Store] activatePanel failed:', e)
    }
  },

  deletePanelRequest: async (id) => {
    try {
      const res = await fetch(`/api/panels/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      })
      if (!res.ok) throw new Error(res.statusText)
      useAppStore.getState().removePanel(id)
    } catch (e) {
      console.error('[Store] deletePanel failed:', e)
    }
  },
}))
