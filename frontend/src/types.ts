export interface PadEntry {
  preset: string
  note: number
  label: string
  source: string
  action_type: string
  action_data: Record<string, string>
  hotkey: string
  locked: boolean
  color: [number, number, number]
  toggle_state?: boolean | null  // true=ON, false=OFF, null/undefined=not a toggle
}

export interface KnobEntry {
  cc: number
  label: string
  action: { type: string; target: string }
  value: number
}

export interface PresetInfo {
  index: number
  name: string
}

export interface PluginInfo {
  name: string
  version: string
  description: string
  enabled: boolean
}

export interface LogEntry {
  tag: string
  message: string
  color: [number, number, number]
  ts: number
}

export interface ObsState {
  connected: boolean
  current_scene: string
  is_recording: boolean
  is_streaming: boolean
  is_replay_buffer_active: boolean
  scenes: string[]
}

export interface CatalogAction {
  id: string
  type: string
  target: string
  label: string
  description: string
  params_schema: Record<string, unknown>
}

export interface KnobCatalog {
  core: CatalogAction[]
  plugins: Record<string, CatalogAction[]>
}

export interface PanelPresetState {
  preset: string
  order: number[]
  bank?: string
}

export type PanelType = 'pad' | 'knob'
export type PanelBank = 'A' | 'B'

export interface Panel {
  instanceId: string
  type: PanelType
  bank: PanelBank
  preset: string
  title: string
}

export type ActivePanelsMap = Partial<Record<
  'pad:A' | 'pad:B' | 'knob:A' | 'knob:B',
  string | null
>>

export interface AppState {
  midi: { connected: boolean; port_name: string | null; device_name: string }
  presets: { current_index: number; list: PresetInfo[] }
  pads: Record<string, PadEntry>
  knobs: KnobEntry[]
  plugins: { discovered: PluginInfo[] }
  obs: ObsState
  logs: LogEntry[]
  panel_presets?: Record<string, PanelPresetState>
  active_midi_presets?: Record<string, string>
  active_knob_presets?: Record<string, string>
}

export interface WsEvent {
  type: 'event'
  event: string
  payload: Record<string, unknown>
  ts: number
}

export interface WsResponse {
  type: 'response'
  id: string
  status: 'ok' | 'error'
  payload: AppState
}
