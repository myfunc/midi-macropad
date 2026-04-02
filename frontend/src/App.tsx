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
import { PadGridPanel } from './panels/PadGridPanel'
import { PropertiesPanel } from './panels/PropertiesPanel'
import { LogPanel } from './panels/LogPanel'
import { ObsPanel } from './panels/ObsPanel'
import { VoicemeeterPanel } from './panels/VoicemeeterPanel'
import { VoiceScribePanel } from './panels/VoiceScribePanel'
import { SettingsPanel } from './panels/SettingsPanel'

const components: Record<string, React.FC<any>> = {
  padgrid: PadGridPanel,
  properties: PropertiesPanel,
  log: LogPanel,
  obs: ObsPanel,
  voicemeeter: VoicemeeterPanel,
  voicescribe: VoiceScribePanel,
  settings: SettingsPanel,
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

const PANEL_CATALOG = [
  { id: 'padgrid', title: 'Pad Grid' },
  { id: 'properties', title: 'Properties' },
  { id: 'log', title: 'Log' },
  { id: 'obs', title: 'OBS' },
  { id: 'voicemeeter', title: 'Voicemeeter' },
  { id: 'voicescribe', title: 'Voice Scribe' },
  { id: 'settings', title: 'Settings' },
] as const

export default function App() {
  const dockApiRef = useRef<DockviewApi | null>(null)
  const { isLeader, otherTabExists } = useTabLeader()
  const [layoutLoaded, setLayoutLoaded] = useState(false)

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

    // Default layout
    buildDefaultLayout(api)
    setLayoutLoaded(true)
    setupLayoutSave(api)
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
    const padgrid = api.addPanel({
      id: 'padgrid', component: 'padgrid', title: 'Pad Grid',
    })
    api.addPanel({
      id: 'properties', component: 'properties', title: 'Properties',
      position: { referencePanel: padgrid, direction: 'right' },
      initialWidth: 280,
    })
    const log = api.addPanel({
      id: 'log', component: 'log', title: 'Log',
      position: { referencePanel: padgrid, direction: 'below' },
      initialHeight: 200,
    })
    api.addPanel({
      id: 'obs', component: 'obs', title: 'OBS',
      position: { referencePanel: log, direction: 'right' },
    })
  }

  function togglePanel(id: string, title: string) {
    const api = dockApiRef.current
    if (!api) return
    const existing = api.getPanel(id)
    if (existing) {
      existing.api.setActive()
    } else {
      api.addPanel({ id, component: id, title, floating: { width: 400, height: 500 } })
    }
  }

  function resetLayout() {
    localStorage.removeItem('dockview-layout')
    // Also clear backend
    fetch('/api/settings/ui_layout', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: null }),
    }).catch(() => {})
    window.location.reload()
  }

  return (
    <WebSocketProvider>
      <div className="app-root">
        {otherTabExists && !isLeader && (
          <div className="tab-warning">
            Another tab is active — this tab is read-only (layout changes won't save)
          </div>
        )}
        <PresetBar
          onOpenSettings={() => togglePanel('settings', 'Settings')}
          onTogglePanel={togglePanel}
          onResetLayout={resetLayout}
          panels={PANEL_CATALOG}
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
