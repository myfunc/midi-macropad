import { useCallback, useEffect, useRef, useState } from 'react'
import {
  DockviewReact,
  type DockviewReadyEvent,
  type DockviewApi,
  type IWatermarkPanelProps,
} from 'dockview-react'
import 'dockview-core/dist/styles/dockview.css'
import { WebSocketProvider } from './ws/WebSocketProvider'
import { PresetBar } from './components/PresetBar'
import { StatusBar } from './components/StatusBar'
import { useAppStore } from './stores/useAppStore'
import { useTabLeader } from './hooks/useTabLeader'
import { loadLayout, saveLayout } from './hooks/useLayoutPersistence'
import { PadPanel } from './panels/PadPanel'
import { KnobPanel } from './panels/KnobPanel'
import { PropertiesPanel } from './panels/PropertiesPanel'
import { LogPanel } from './panels/LogPanel'
import { ObsPanel } from './panels/ObsPanel'
import { VoicemeeterPanel } from './panels/VoicemeeterPanel'
import { VoiceScribePanel } from './panels/VoiceScribePanel'
import { SettingsPanel } from './panels/SettingsPanel'
import { PianoPanel } from './panels/PianoPanel'

const components: Record<string, React.FC<any>> = {
  padPanel: PadPanel,
  knobPanel: KnobPanel,
  piano: PianoPanel,
  properties: PropertiesPanel,
  log: LogPanel,
  obs: ObsPanel,
  voicemeeter: VoicemeeterPanel,
  voicescribe: VoiceScribePanel,
  settings: SettingsPanel,
  // Legacy aliases — older saved layouts may reference these
  padBank: PadPanel,
  knobBank: KnobPanel,
  bankA: PadPanel,
  bankB: PadPanel,
  knobs: KnobPanel,
}

function Watermark(_props: IWatermarkPanelProps) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100%', color: '#707088', fontSize: 14,
    }}>
      MIDI Macropad
    </div>
  )
}

export type MenuAction =
  | { kind: 'addPad' }
  | { kind: 'addKnob' }
  | { kind: 'togglePanel'; id: string; title: string; instanceId?: string }
  | { kind: 'resetLayout' }

export type MenuLeaf = {
  label: string
  action: MenuAction
}

export type MenuGroup = {
  label: string
  items: MenuLeaf[]
}

export const MENU_GROUPS: MenuGroup[] = [
  { label: 'Controls', items: [
    { label: 'Add Pad Panel', action: { kind: 'addPad' } },
    { label: 'Add Knob Panel', action: { kind: 'addKnob' } },
  ]},
  { label: 'Plugins', items: [
    { label: 'Piano', action: { kind: 'togglePanel', id: 'piano', title: 'Piano' } },
    { label: 'Voice Scribe', action: { kind: 'togglePanel', id: 'voicescribe', title: 'Voice Scribe' } },
    { label: 'OBS', action: { kind: 'togglePanel', id: 'obs', title: 'OBS' } },
    { label: 'Voicemeeter', action: { kind: 'togglePanel', id: 'voicemeeter', title: 'Voicemeeter' } },
  ]},
  { label: 'Settings', items: [
    { label: 'Settings', action: { kind: 'togglePanel', id: 'settings', title: 'Settings' } },
    { label: 'Properties', action: { kind: 'togglePanel', id: 'properties', title: 'Properties' } },
  ]},
  { label: 'Logs', items: [
    { label: 'Log', action: { kind: 'togglePanel', id: 'log', title: 'Log' } },
  ]},
]

export default function App() {
  const dockApiRef = useRef<DockviewApi | null>(null)
  const { isLeader, otherTabExists } = useTabLeader()
  const [, setLayoutLoaded] = useState(false)

  const ensurePanelOpen = useCallback((instanceId: string, component: string, title: string) => {
    const api = dockApiRef.current
    if (!api) return
    const existing = api.getPanel(instanceId)
    if (existing) {
      existing.api.setActive()
      return
    }
    api.addPanel({
      id: instanceId,
      component,
      title,
      floating: { width: 400, height: 400 },
    })
  }, [])

  const onReady = useCallback(async (event: DockviewReadyEvent) => {
    const api = event.api
    dockApiRef.current = api

    // Load saved layout (backend first, then localStorage)
    const saved = await loadLayout()
    if (saved) {
      try {
        api.fromJSON(saved as any)
        setLayoutLoaded(true)
        setupLayoutSave(api)
        return
      } catch {
        // Corrupted layout — fall through
      }
    }

    // Default layout — builds from known panels in store, else placeholders
    buildDefaultLayout(api)
    setLayoutLoaded(true)
    setupLayoutSave(api)
  }, [])

  // When panels are created (from menu or migration), open them in dockview
  useEffect(() => {
    const unsub = useAppStore.subscribe((state, prev) => {
      const api = dockApiRef.current
      if (!api) return
      // Detect newly added panels
      for (const [id, panel] of Object.entries(state.panels)) {
        if (!prev.panels[id]) {
          const component = panel.type === 'pad' ? 'padPanel' : 'knobPanel'
          if (!api.getPanel(id)) {
            api.addPanel({
              id, component, title: panel.title,
              floating: { width: 420, height: 360 },
            })
          }
        }
      }
      // Detect removed panels
      for (const id of Object.keys(prev.panels)) {
        if (!state.panels[id]) {
          const p = api.getPanel(id)
          if (p) api.removePanel(p)
        }
      }
    })
    return unsub
  }, [])

  function setupLayoutSave(api: DockviewApi) {
    api.onDidLayoutChange(() => {
      try {
        saveLayout(api.toJSON(), isLeader)
      } catch {
        // ignore
      }
    })
  }

  function buildDefaultLayout(api: DockviewApi) {
    const panels = Object.values(useAppStore.getState().panels)
    const padPanels = panels.filter(p => p.type === 'pad')
    const knobPanels = panels.filter(p => p.type === 'knob')

    let reference: string | null = null
    const firstPad = padPanels[0]
    if (firstPad) {
      api.addPanel({
        id: firstPad.instanceId, component: 'padPanel', title: firstPad.title,
      })
      reference = firstPad.instanceId
    }
    for (const p of padPanels.slice(1)) {
      api.addPanel({
        id: p.instanceId, component: 'padPanel', title: p.title,
        position: reference ? { referencePanel: reference, direction: 'right' } : undefined,
      })
      reference = p.instanceId
    }

    let knobRef: string | null = null
    for (const k of knobPanels) {
      api.addPanel({
        id: k.instanceId, component: 'knobPanel', title: k.title,
        position: reference
          ? { referencePanel: knobRef ?? reference, direction: knobRef ? 'below' : 'right' }
          : undefined,
      })
      knobRef = k.instanceId
    }

    // Utility panels (properties, log) only if there's space
    if (reference) {
      api.addPanel({
        id: 'properties', component: 'properties', title: 'Properties',
        position: { referencePanel: reference, direction: 'right' },
        initialWidth: 280,
      })
      api.addPanel({
        id: 'log', component: 'log', title: 'Log',
        position: { referencePanel: reference, direction: 'below' },
        initialHeight: 180,
      })
    }
  }

  const handleMenuAction = useCallback(async (action: MenuAction) => {
    const store = useAppStore.getState()
    if (action.kind === 'addPad') {
      await store.createPanelRequest('pad', 'A')
    } else if (action.kind === 'addKnob') {
      await store.createPanelRequest('knob', 'A')
    } else if (action.kind === 'togglePanel') {
      ensurePanelOpen(action.instanceId ?? action.id, action.id, action.title)
    } else if (action.kind === 'resetLayout') {
      localStorage.removeItem('dockview-layout')
      fetch('/api/settings/ui_layout', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: null }),
      }).catch(() => {})
      window.location.reload()
    }
  }, [ensurePanelOpen])

  return (
    <WebSocketProvider>
      <div className="app-root">
        {otherTabExists && !isLeader && (
          <div className="tab-warning">
            Another tab is active — this tab is read-only (layout changes won't save)
          </div>
        )}
        <PresetBar
          onMenuAction={handleMenuAction}
          menu={MENU_GROUPS}
        />
        <div className="dock-container">
          <DockviewReact
            components={components}
            watermarkComponent={Watermark}
            onReady={onReady}
            className="dockview-theme-dark"
          />
        </div>
        <StatusBar />
        <Toast />
      </div>
    </WebSocketProvider>
  )
}

function Toast() {
  const message = useAppStore(s => s.toastMessage)
  const clearToast = useAppStore(s => s.clearToast)

  useEffect(() => {
    if (message) {
      const t = setTimeout(clearToast, 4000)
      return () => clearTimeout(t)
    }
  }, [message, clearToast])

  if (!message) return null

  return (
    <div className="toast-msg">
      {message}
    </div>
  )
}
