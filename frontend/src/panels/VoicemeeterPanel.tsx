import { useState, useEffect, useRef } from 'react'
import type { IDockviewPanelProps } from 'dockview-react'

interface VmState {
  connected: boolean
  mic_mute: boolean
  desk_mute: boolean
  eq_on: boolean
  send2mic: boolean
  gate_on: boolean
  monitor: boolean
  comp_on: boolean
  mic_gain: number
  duck_enabled: boolean
}

const DEFAULT_VM: VmState = {
  connected: false, mic_mute: false, desk_mute: false,
  eq_on: false, send2mic: false, gate_on: false,
  monitor: false, comp_on: false, mic_gain: 0, duck_enabled: false,
}

export function VoicemeeterPanel(_props: IDockviewPanelProps) {
  const [vm, setVm] = useState<VmState>(DEFAULT_VM)
  const intervalRef = useRef<number | null>(null)

  // Poll VM state from backend (since poll() is disabled in headless)
  useEffect(() => {
    function fetchState() {
      fetch('/api/state')
        .then(r => r.json())
        .then(data => {
          if (data.voicemeeter) setVm(data.voicemeeter)
        })
        .catch(() => {})
    }
    fetchState()
    intervalRef.current = window.setInterval(fetchState, 3000)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [])

  return (
    <div className="vm-panel-content">
      <div className="pp-header">
        <strong>Voicemeeter Banana</strong>
        <span className={`pp-status ${vm.connected ? 'ok' : 'err'}`}>
          {vm.connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>

      {/* Input Strips */}
      <div className="vm-section-title">Input Strips</div>
      <div className="vm-strips">
        <VmStrip name="MIC" color="#64B4FF" device="Yeti Stereo Mic"
          muted={vm.mic_mute} gain={vm.mic_gain}
          busA1={vm.monitor} busB1={!vm.mic_mute} />
        <VmStrip name="DESKTOP" color="#5AE68C" device="VB-Audio Input"
          muted={vm.desk_mute} gain={0}
          busA1={true} busB1={false} />
        <VmStrip name="SEND2MIC" color="#B482FF" device="VB-Audio AUX"
          muted={false} gain={-6}
          busA1={true} busB1={vm.send2mic} />
      </div>

      {/* Output Buses */}
      <div className="vm-section-title">Output Buses</div>
      <div className="vm-strips">
        <div className="vm-strip-row">
          <span className="vm-strip-name" style={{ color: '#64B4FF' }}>A1</span>
          <span className="vm-strip-device">Headphones (Intel SST)</span>
          <span className="vm-badge" style={{ background: 'rgba(100,180,255,0.2)', color: '#64B4FF' }}>HPH</span>
        </div>
        <div className="vm-strip-row">
          <span className="vm-strip-name" style={{ color: '#5AE68C' }}>B1</span>
          <span className="vm-strip-device">Virtual Mic Out</span>
          <span className="vm-badge" style={{ background: 'rgba(90,230,140,0.2)', color: '#5AE68C' }}>MIC</span>
        </div>
      </div>

      {/* Processing */}
      <div className="vm-section-title">Processing (MIC)</div>
      <div className="vm-proc-row">
        <span className={`vm-proc-badge ${vm.gate_on ? 'on' : 'off'}`}>Gate {vm.gate_on ? 'ON' : 'OFF'}</span>
        <span className={`vm-proc-badge ${vm.comp_on ? 'on' : 'off'}`}>Comp {vm.comp_on ? 'ON' : 'OFF'}</span>
        <span className={`vm-proc-badge ${vm.eq_on ? 'on' : 'off'}`}>EQ {vm.eq_on ? 'ON' : 'OFF'}</span>
        <span className={`vm-proc-badge ${vm.monitor ? 'on' : 'off'}`}>Mon {vm.monitor ? 'ON' : 'OFF'}</span>
      </div>

      {/* Audience */}
      <div className="vm-section-title">Audience</div>
      <div className="vm-audience">
        <div className="vm-audience-row">
          <span className="vm-audience-icon">&#127911;</span>
          <span className="vm-audience-label">You hear:</span>
          <span className="vm-audience-chips">
            <span className="vm-chip hear">Desktop</span>
            <span className="vm-chip hear">Send2Mic</span>
            {vm.monitor && <span className="vm-chip hear">Mic (mon)</span>}
          </span>
        </div>
        <div className="vm-audience-row">
          <span className="vm-audience-icon">&#127908;</span>
          <span className="vm-audience-label">Listener gets:</span>
          <span className="vm-audience-chips">
            <span className={`vm-chip ${vm.mic_mute ? 'muted' : 'send'}`}>
              Mic{vm.mic_mute ? ' (muted)' : ''}
            </span>
            {vm.send2mic && <span className="vm-chip send">Send2Mic</span>}
          </span>
        </div>
      </div>

      {/* Ducking */}
      <div className="vm-section-title">Ducking</div>
      <div className="vm-ducking-row">
        <span className={`vm-proc-badge ${vm.duck_enabled ? 'on' : 'off'}`}>
          {vm.duck_enabled ? 'Enabled' : 'Disabled'}
        </span>
      </div>

      {/* Gain */}
      <div className="vm-section-title">Mic Gain</div>
      <div className="settings-row">
        <span className="settings-value" style={{ fontFamily: 'monospace' }}>
          {vm.mic_gain >= 0 ? '+' : ''}{vm.mic_gain.toFixed(1)} dB
        </span>
      </div>
    </div>
  )
}

function VmStrip({ name, color, device, muted, gain, busA1, busB1 }: {
  name: string; color: string; device: string
  muted: boolean; gain: number; busA1: boolean; busB1: boolean
}) {
  return (
    <div className="vm-strip-row">
      <span className={`vm-mute-indicator ${muted ? 'muted' : 'live'}`}>
        {muted ? 'M' : ''}
      </span>
      <span className="vm-strip-name" style={{ color }}>{name}</span>
      <span className="vm-strip-device">{device}</span>
      <span className="vm-strip-gain">
        {gain >= 0 ? '+' : ''}{gain.toFixed(1)}
      </span>
      <span className="vm-bus-badges">
        <span className={`vm-bus-badge ${busA1 ? 'active' : 'inactive'}`}>A1</span>
        <span className={`vm-bus-badge ${busB1 ? 'active' : 'inactive'}`}>B1</span>
      </span>
    </div>
  )
}
