import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react'
import ReconnectingWebSocket from 'reconnecting-websocket'
import { useAppStore } from '../stores/useAppStore'
import type { WsEvent, WsResponse, LogEntry } from '../types'

interface WsContextValue {
  send: (data: Record<string, unknown>) => void
}

const WsContext = createContext<WsContextValue>({ send: () => {} })

export function useWs() {
  return useContext(WsContext)
}

function getWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws`
}

/**
 * Build a composite flash key from a MIDI note by finding which preset
 * currently owns that note via activeMidiPresets routing.
 * Bank A = notes 16-23, Bank B = notes 24-31.
 */
function flashKeyForNote(note: number): string | null {
  const state = useAppStore.getState()
  let bank: 'A' | 'B' | null = null
  if (note >= 16 && note <= 23) bank = 'A'
  else if (note >= 24 && note <= 31) bank = 'B'
  if (!bank) return null

  // Resolve active pad panel for this bank
  const slot = (`pad:${bank}`) as 'pad:A' | 'pad:B'
  const activeId = state.activePanels[slot]
  if (activeId) {
    const preset = state.panels[activeId]?.preset
    if (preset) return `${preset}:${note}`
  }
  // Legacy fallback
  const legacyKey = bank === 'A' ? 'bankA' : 'bankB'
  const preset = state.activeMidiPresets[legacyKey] ?? state.panelPresets[legacyKey]?.preset
  if (!preset) return null
  return `${preset}:${note}`
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const wsRef = useRef<ReconnectingWebSocket | null>(null)
  const store = useAppStore

  useEffect(() => {
    const ws = new ReconnectingWebSocket(getWsUrl(), [], {
      maxReconnectionDelay: 5000,
      minReconnectionDelay: 1000,
      reconnectionDelayGrowFactor: 1.5,
      maxRetries: Infinity,
    })

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        handleMessage(msg)
      } catch {
        // ignore malformed messages
      }
    }

    ws.onopen = () => {
      store.getState().addLog({
        tag: 'WS', message: 'Connected to backend',
        color: [100, 255, 150], ts: Date.now() / 1000,
      })
    }

    ws.onclose = () => {
      store.getState().addLog({
        tag: 'WS', message: 'Disconnected from backend',
        color: [255, 180, 80], ts: Date.now() / 1000,
      })
    }

    wsRef.current = ws

    return () => {
      ws.close()
    }
  }, [])

  function handleMessage(msg: WsResponse | WsEvent) {
    const s = store.getState()

    if (msg.type === 'response' && 'id' in msg && msg.id === 'handshake') {
      const payload = msg.payload as unknown as Record<string, unknown>
      console.log('[WS] Handshake received, presets:', (payload.presets as any)?.list?.length)
      s.setInitialState(payload)
      // Parse active_midi_presets from handshake
      if (payload.active_midi_presets) {
        s.setActiveMidiPresets(payload.active_midi_presets as Record<string, string>)
      }
      // Parse active_knob_presets so panel routing survives reconnects
      if (payload.active_knob_presets) {
        s.setActiveKnobPresets(payload.active_knob_presets as Record<string, string>)
      }
      // Freeform panels from snapshot
      if (payload.panels) {
        s.setPanels(payload.panels as Record<string, any>)
      }
      if (payload.active_panels) {
        s.setActivePanels(payload.active_panels as Record<string, string>)
      }
      return
    }

    if (msg.type === 'event') {
      const evt = msg as WsEvent
      switch (evt.event) {
        case 'midi.pad_press': {
          const note = evt.payload.note as number
          const key = flashKeyForNote(note)
          if (key) s.flashPad(key)
          break
        }
        case 'midi.pad_release': {
          const note = evt.payload.note as number
          const key = flashKeyForNote(note)
          if (key) s.releasePad(key)
          break
        }
        case 'midi.knob':
          s.updateKnob(evt.payload.cc as number, evt.payload.value as number)
          break
        case 'log.entry':
          s.addLog(evt.payload as unknown as LogEntry)
          break
        case 'preset.changed':
          s.setPresetIndex(evt.payload.index as number)
          if (evt.payload.pads) {
            s.updatePads(evt.payload.pads as Record<string, any>)
          }
          break
        case 'pads.updated':
          if (evt.payload.pads) {
            s.updatePads(evt.payload.pads as Record<string, any>)
          }
          if (evt.payload.active_midi_presets) {
            s.setActiveMidiPresets(evt.payload.active_midi_presets as Record<string, string>)
          }
          break
        case 'obs.scene_changed':
          s.updateObs({ current_scene: evt.payload.scene as string })
          break
        case 'obs.record_state':
          s.updateObs({ is_recording: evt.payload.recording as boolean })
          break
        case 'midi.status':
          s.setInitialState({
            ...s, midi: {
              connected: evt.payload.connected,
              port_name: evt.payload.port_name,
              device_name: s.midiDeviceName,
            }
          } as any)
          break
        case 'presets.changed':
          s.fetchPresets()
          break
        case 'panel_preset.changed': {
          const panel = evt.payload.panel as string
          const preset = evt.payload.preset as string
          if (panel && preset) {
            s.setPanelPreset(panel, preset)
          }
          if (evt.payload.pads) {
            s.updatePads(evt.payload.pads as Record<string, any>)
          }
          if (evt.payload.active_midi_presets) {
            s.setActiveMidiPresets(evt.payload.active_midi_presets as Record<string, string>)
          }
          break
        }
        case 'knobs.updated': {
          const cc = evt.payload.cc as number
          const label = evt.payload.label as string
          if (cc !== undefined && label) {
            s.updateKnobLabel(cc, label)
          }
          s.fetchKnobCatalog()
          break
        }
        case 'knobs.updated_all': {
          if (evt.payload.knobs && Array.isArray(evt.payload.knobs)) {
            store.setState({ knobs: evt.payload.knobs })
          }
          break
        }
        case 'knob_preset.changed': {
          const fallbackPanel =
            s.activePanels['knob:A'] ??
            s.activePanels['knob:B'] ??
            'knobBank-A'
          const panel = (evt.payload.panel as string) || fallbackPanel
          if (evt.payload.knobs && Array.isArray(evt.payload.knobs)) {
            store.setState({ knobs: evt.payload.knobs })
          }
          const kPreset = evt.payload.preset as string
          if (kPreset) {
            s.setPanelPreset(panel, kPreset)
          }
          if (evt.payload.knob_routing) {
            s.setActiveKnobPresets(evt.payload.knob_routing as Record<string, string>)
          }
          break
        }
        case 'piano.note_on': {
          const note = evt.payload.note as number
          if (note !== undefined) s.setPianoKey(note, true)
          break
        }
        case 'piano.note_off': {
          const note = evt.payload.note as number
          if (note !== undefined) s.setPianoKey(note, false)
          break
        }
        case 'fx.param_changed': {
          // Store FX param changes for future Piano UI display
          console.log('[WS] FX param changed:', evt.payload)
          break
        }
        case 'ops.update':
          console.log('[WS] Op update:', evt.payload)
          break
        case 'panel.created': {
          const panel = evt.payload.panel as any
          if (panel?.instanceId) s.upsertPanel(panel)
          break
        }
        case 'panel.updated': {
          const panel = evt.payload.panel as any
          if (panel?.instanceId) s.upsertPanel(panel)
          if (evt.payload.pads) {
            s.updatePads(evt.payload.pads as Record<string, any>)
          }
          if (evt.payload.active_midi_presets) {
            s.setActiveMidiPresets(evt.payload.active_midi_presets as Record<string, string>)
          }
          if (evt.payload.active_knob_presets) {
            s.setActiveKnobPresets(evt.payload.active_knob_presets as Record<string, string>)
          }
          break
        }
        case 'panel.deleted': {
          const id = evt.payload.instanceId as string
          if (id) s.removePanel(id)
          if (evt.payload.active_panels) {
            s.setActivePanels(evt.payload.active_panels as Record<string, string>)
          }
          break
        }
        case 'panel.activated': {
          if (evt.payload.active_panels) {
            s.setActivePanels(evt.payload.active_panels as Record<string, string>)
          }
          if (evt.payload.pads) {
            s.updatePads(evt.payload.pads as Record<string, any>)
          }
          if (evt.payload.active_midi_presets) {
            s.setActiveMidiPresets(evt.payload.active_midi_presets as Record<string, string>)
          }
          if (evt.payload.active_knob_presets) {
            s.setActiveKnobPresets(evt.payload.active_knob_presets as Record<string, string>)
          }
          break
        }
        case 'error.unhandled':
          s.showToast(`Error: ${evt.payload.error}`)
          setTimeout(() => s.clearToast(), 5000)
          break
      }
    }
  }

  const contextValue: WsContextValue = {
    send: (data) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(data))
      }
    },
  }

  return (
    <WsContext.Provider value={contextValue}>
      {children}
    </WsContext.Provider>
  )
}
