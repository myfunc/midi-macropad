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
      return
    }

    if (msg.type === 'event') {
      const evt = msg as WsEvent
      switch (evt.event) {
        case 'midi.pad_press':
          s.flashPad(evt.payload.note as number)
          break
        case 'midi.pad_release':
          s.releasePad(evt.payload.note as number)
          break
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
        case 'ops.update':
          console.log('[WS] Op update:', evt.payload)
          break
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
