import { useCallback, useEffect } from 'react'
import {
  DockviewReact,
  type DockviewReadyEvent,
  type IWatermarkPanelProps,
} from 'dockview-react'
import 'dockview-core/dist/styles/dockview.css'
import { WebSocketProvider } from './ws/WebSocketProvider'
import { PresetBar } from './components/PresetBar'
import { StatusBar } from './components/StatusBar'
import { useAppStore } from './stores/useAppStore'
import { PadGridPanel } from './panels/PadGridPanel'
import { PropertiesPanel } from './panels/PropertiesPanel'
import { LogPanel } from './panels/LogPanel'
import { ObsPanel } from './panels/ObsPanel'
import { SettingsPanel } from './panels/SettingsPanel'

const components: Record<string, React.FC<any>> = {
  padgrid: PadGridPanel,
  properties: PropertiesPanel,
  log: LogPanel,
  obs: ObsPanel,
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

export default function App() {
  const onReady = useCallback((event: DockviewReadyEvent) => {
    const api = event.api

    // Try to restore saved layout
    const saved = localStorage.getItem('dockview-layout')
    if (saved) {
      try {
        api.fromJSON(JSON.parse(saved))
        return
      } catch {
        // Fall through to default layout
      }
    }

    // Default layout
    const padgrid = api.addPanel({
      id: 'padgrid',
      component: 'padgrid',
      title: 'Pad Grid',
    })

    api.addPanel({
      id: 'properties',
      component: 'properties',
      title: 'Properties',
      position: { referencePanel: padgrid, direction: 'right' },
      initialWidth: 280,
    })

    const log = api.addPanel({
      id: 'log',
      component: 'log',
      title: 'Log',
      position: { referencePanel: padgrid, direction: 'below' },
      initialHeight: 200,
    })

    api.addPanel({
      id: 'obs',
      component: 'obs',
      title: 'OBS',
      position: { referencePanel: log, direction: 'right' },
    })

    // Save layout on every change
    api.onDidLayoutChange(() => {
      try {
        localStorage.setItem('dockview-layout', JSON.stringify(api.toJSON()))
      } catch {
        // ignore quota errors
      }
    })
  }, [])

  return (
    <WebSocketProvider>
      <div className="app-root">
        <PresetBar />
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
    <div style={{
      position: 'fixed', bottom: 40, left: '50%', transform: 'translateX(-50%)',
      background: '#3A3A52', border: '1px solid #6EB4FF', color: '#6EB4FF',
      padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 500,
      zIndex: 200, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
    }}>
      {message}
    </div>
  )
}
