import { create } from 'zustand'
import type { PadEntry, KnobEntry, PresetInfo, PluginInfo, LogEntry, ObsState } from '../types'

interface AppStore {
  // MIDI
  midiConnected: boolean
  midiPortName: string | null
  midiDeviceName: string

  // Presets
  presets: PresetInfo[]
  currentPresetIndex: number

  // Pads
  pads: Record<string, PadEntry>
  selectedNote: number | null

  // Knobs
  knobs: KnobEntry[]

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

  // Actions
  setInitialState: (state: Record<string, unknown>) => void
  selectPad: (note: number | null) => void
  flashPad: (note: number) => void
  releasePad: (note: number) => void
  updateKnob: (cc: number, value: number) => void
  addLog: (entry: LogEntry) => void
  setPresetIndex: (index: number) => void
  updatePads: (pads: Record<string, PadEntry>) => void
  showToast: (message: string) => void
  clearToast: () => void
  updateObs: (partial: Partial<ObsState>) => void
}

const MAX_LOGS = 500

export const useAppStore = create<AppStore>((set) => ({
  midiConnected: false,
  midiPortName: null,
  midiDeviceName: '',
  presets: [],
  currentPresetIndex: 0,
  pads: {},
  selectedNote: null,
  knobs: [],
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

  setInitialState: (state) => set(() => {
    const s = state as Record<string, any>
    return {
      midiConnected: s.midi?.connected ?? false,
      midiPortName: s.midi?.port_name ?? null,
      midiDeviceName: s.midi?.device_name ?? '',
      presets: s.presets?.list ?? [],
      currentPresetIndex: s.presets?.current_index ?? 0,
      pads: s.pads ?? {},
      knobs: s.knobs ?? [],
      plugins: s.plugins?.discovered ?? [],
      obs: s.obs ?? {},
      logs: s.logs ?? [],
    }
  }),

  selectPad: (note) => set({ selectedNote: note }),

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

  updateObs: (partial) => set((s) => ({
    obs: { ...s.obs, ...partial },
  })),
}))
