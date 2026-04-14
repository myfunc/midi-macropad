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
import type { PanelType, PanelBank } from './types'

const components: Record<string, React.FC<any>> = {
  padPanel: PadPanel,
  knobPanel: KnobPanel,
  piano: PianoPanel,
  pianoPanel: PianoPanel,
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

function panelComponentName(type: PanelType): string {
  if (type === 'pad') return 'padPanel'
  if (type === 'knob') return 'knobPanel'
  return 'pianoPanel'
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
  | { kind: 'addPanel'; type: PanelType; bank: PanelBank }
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
    { label: 'Add Pad Panel A', action: { kind: 'addPanel', type: 'pad', bank: 'A' } },
    { label: 'Add Pad Panel B', action: { kind: 'addPanel', type: 'pad', bank: 'B' } },
    { label: 'Add Knob Panel A', action: { kind: 'addPanel', type: 'knob', bank: 'A' } },
    { label: 'Add Knob Panel B', action: { kind: 'addPanel', type: 'knob', bank: 'B' } },
    { label: 'Add Piano Panel (Play)', action: { kind: 'addPanel', type: 'piano', bank: 'play' } },
    { label: 'Add Piano Panel (Map)', action: { kind: 'addPanel', type: 'piano', bank: 'map' } },
  ]},
  { label: 'Plugins', items: [
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

  // Tracks whether onReady completed so the store subscription doesn't spawn
  // "ghost" panels during initial hydration.
  const layoutReadyRef = useRef(false)
  // Tracks which panel-ids are currently represented in the dockview UI.
  // Updated on onReady, subscription add, and panel deletion events.
  const knownPanelIdsRef = useRef<Set<string>>(new Set())

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
    })
    knownPanelIdsRef.current.add(instanceId)
  }, [])

  /**
   * One-shot reconciliation: mounts any panels that the store learned about
   * BEFORE ``layoutReadyRef`` flipped true (e.g. a WS handshake whose
   * ``setPanels`` fired while ``loadLayout()`` was still awaiting). The
   * zustand subscription only observes *subsequent* mutations, so without
   * this pass such panels would be stranded in the store with no dockview
   * tab until a full reload.
   */
  const reconcileStorePanels = useCallback((api: DockviewApi) => {
    const storePanels = useAppStore.getState().panels
    for (const [id, p] of Object.entries(storePanels)) {
      if (knownPanelIdsRef.current.has(id)) continue
      if (api.getPanel(id)) {
        knownPanelIdsRef.current.add(id)
        continue
      }
      try {
        api.addPanel({
          id,
          component: panelComponentName(p.type),
          title: p.title,
        })
        knownPanelIdsRef.current.add(id)
      } catch (e) {
        console.warn('[Layout] reconcile addPanel failed for', id, e)
      }
    }
  }, [])

  const onReady = useCallback(async (event: DockviewReadyEvent) => {
    const api = event.api
    dockApiRef.current = api

    // Collect currently-known panel ids from the store (plus utilities) so
    // loadLayout() can strip orphans before handing JSON back to dockview.
    const storePanels = Object.keys(useAppStore.getState().panels)
    const saved = await loadLayout(storePanels)
    if (saved) {
      try {
        api.fromJSON(saved as any)
        // Seed knownPanelIds from the freshly-restored dockview state.
        for (const p of api.panels) knownPanelIdsRef.current.add(p.id)
        layoutReadyRef.current = true
        reconcileStorePanels(api)
        setLayoutLoaded(true)
        setupLayoutSave(api)
        return
      } catch {
        // Corrupted layout — fall through
      }
    }

    // Default layout — builds from known panels in store, else placeholders
    buildDefaultLayout(api)
    for (const p of api.panels) knownPanelIdsRef.current.add(p.id)
    layoutReadyRef.current = true
    reconcileStorePanels(api)
    setLayoutLoaded(true)
    setupLayoutSave(api)
  }, [reconcileStorePanels])

  // When panels are created (from menu, migration, WS) or deleted, sync the
  // dockview state. This subscription no longer uses ``floating`` — new panels
  // are docked into the grid as regular tabs.
  useEffect(() => {
    const unsub = useAppStore.subscribe((state, prev) => {
      const api = dockApiRef.current
      if (!api) return
      if (!layoutReadyRef.current) return

      // Detect newly added panels — open only if truly new.
      for (const [id, panel] of Object.entries(state.panels)) {
        if (prev.panels[id]) continue
        if (knownPanelIdsRef.current.has(id)) continue
        if (api.getPanel(id)) {
          knownPanelIdsRef.current.add(id)
          continue
        }
        const component = panelComponentName(panel.type)
        api.addPanel({
          id, component, title: panel.title,
        })
        knownPanelIdsRef.current.add(id)
      }

      // Detect removed panels — close dockview entry and drop from known set.
      for (const id of Object.keys(prev.panels)) {
        if (state.panels[id]) continue
        const p = api.getPanel(id)
        if (p) api.removePanel(p)
        knownPanelIdsRef.current.delete(id)
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

  /**
   * Build a deterministic default layout (no floating panels):
   * - 2x2 grid: padA top-left, padB right-of-padA, knobA below-padA,
   *   knobB below-padB.
   * - Properties + Log in a right-side column.
   * Missing panels are skipped gracefully.
   */
  function buildDefaultLayout(api: DockviewApi) {
    const panels = Object.values(useAppStore.getState().panels)
    const padA = panels.find(p => p.type === 'pad' && p.bank === 'A')
    const padB = panels.find(p => p.type === 'pad' && p.bank === 'B')
    const knobA = panels.find(p => p.type === 'knob' && p.bank === 'A')
    const knobB = panels.find(p => p.type === 'knob' && p.bank === 'B')

    let anchor: string | null = null

    if (padA) {
      api.addPanel({
        id: padA.instanceId, component: 'padPanel', title: padA.title,
      })
      anchor = padA.instanceId
    }
    if (padB) {
      api.addPanel({
        id: padB.instanceId, component: 'padPanel', title: padB.title,
        position: anchor
          ? { referencePanel: anchor, direction: 'right' }
          : undefined,
      })
      if (!anchor) anchor = padB.instanceId
    }
    if (knobA) {
      api.addPanel({
        id: knobA.instanceId, component: 'knobPanel', title: knobA.title,
        position: padA
          ? { referencePanel: padA.instanceId, direction: 'below' }
          : (anchor ? { referencePanel: anchor, direction: 'below' } : undefined),
      })
      if (!anchor) anchor = knobA.instanceId
    }
    if (knobB) {
      api.addPanel({
        id: knobB.instanceId, component: 'knobPanel', title: knobB.title,
        position: padB
          ? { referencePanel: padB.instanceId, direction: 'below' }
          : (knobA
            ? { referencePanel: knobA.instanceId, direction: 'right' }
            : (anchor ? { referencePanel: anchor, direction: 'right' } : undefined)),
      })
      if (!anchor) anchor = knobB.instanceId
    }

    // Utility panels (properties, log) — side column.
    if (anchor) {
      api.addPanel({
        id: 'properties', component: 'properties', title: 'Properties',
        position: { referencePanel: anchor, direction: 'right' },
        initialWidth: 280,
      })
      api.addPanel({
        id: 'log', component: 'log', title: 'Log',
        position: { referencePanel: 'properties', direction: 'below' },
        initialHeight: 180,
      })
    }
  }

  const handleMenuAction = useCallback(async (action: MenuAction) => {
    const store = useAppStore.getState()
    if (action.kind === 'addPanel') {
      await store.createPanelRequest(action.type, action.bank)
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
