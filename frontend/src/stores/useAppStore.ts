import { create } from 'zustand'
import type { PadEntry, KnobEntry, PresetInfo, PluginInfo, LogEntry, ObsState, KnobCatalog, PanelPresetState } from '../types'

interface AppStore {
  // MIDI
  midiConnected: boolean
  midiPortName: string | null
  midiDeviceName: string

  // Presets
  presets: PresetInfo[]
  knobPresets: PresetInfo[]
  currentPresetIndex: number

  // Pads
  pads: Record<string, PadEntry>
  selectedNote: number | null

  // Knobs
  knobs: KnobEntry[]
  selectedKnobCC: number | null

  // Plugins
  plugins: PluginInfo[]

  // OBS
  obs: ObsState

  // Logs
  logs: LogEntry[]

  // Flash state (which pads are currently pressed)
  flashedPads: Set<number>

  // Toast
  toastMessage: string | null

  // Panel presets (per-panel independent preset + order)
  panelPresets: Record<string, PanelPresetState>

  // Knob catalog (cached)
  knobCatalog: KnobCatalog | null
  knobCatalogLoading: boolean

  // Actions
  setInitialState: (state: Record<string, unknown>) => void
  selectPad: (note: number | null) => void
  selectKnob: (cc: number | null) => void
  flashPad: (note: number) => void
  releasePad: (note: number) => void
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
  selectedNote: null,
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
  knobCatalog: null,
  knobCatalogLoading: false,

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
    }
  }),

  selectPad: (note) => set({ selectedNote: note, selectedKnobCC: null }),
  selectKnob: (cc) => set({ selectedKnobCC: cc, selectedNote: null }),

  flashPad: (note) => set((s) => {
    const next = new Set(s.flashedPads)
    next.add(note)
    return { flashedPads: next }
  }),

  releasePad: (note) => set((s) => {
    const next = new Set(s.flashedPads)
    next.delete(note)
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
}))
