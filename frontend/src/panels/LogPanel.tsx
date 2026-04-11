import { useEffect, useMemo, useRef } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { LogEntry } from '../types'
import type { IDockviewPanelProps } from 'dockview-react'

const PREFIX_COLORS: Record<string, string> = {
  PAD: '#5AE68C', KNOB: '#B482FF', OBS: '#FFA54F',
  SYS: '#64B4FF', VM: '#5AE68C', MIDI: '#64B4FF',
  WS: '#64B4FF', ERR: '#FF7878',
}

interface GroupedEntry {
  entry: LogEntry
  count: number
  startValue: string | null
  endValue: string | null
}

function splitMessage(msg: string): { signature: string; value: string | null } {
  const eq = msg.lastIndexOf('=')
  if (eq === -1) return { signature: msg, value: null }
  return { signature: msg.slice(0, eq), value: msg.slice(eq + 1) }
}

function groupLogs(logs: LogEntry[]): GroupedEntry[] {
  const result: GroupedEntry[] = []
  let lastSig: string | null = null

  for (const entry of logs) {
    const { signature, value } = splitMessage(entry.message)
    const sig = `${entry.tag}\0${signature}`

    if (sig === lastSig && result.length > 0) {
      const g = result[result.length - 1]
      g.count += 1
      g.entry = entry
      if (value !== null) g.endValue = value
    } else {
      result.push({ entry, count: 1, startValue: value, endValue: value })
      lastSig = sig
    }
  }
  return result
}

export function LogPanel(_props: IDockviewPanelProps) {
  const logs = useAppStore(s => s.logs)
  const containerRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)

  const grouped = useMemo(() => groupLogs(logs), [logs])

  useEffect(() => {
    if (autoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [grouped])

  function onScroll() {
    const el = containerRef.current
    if (!el) return
    autoScrollRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 30
  }

  return (
    <div className="log-container" ref={containerRef} onScroll={onScroll}>
      {grouped.map((g, i) => {
        const { entry, count, startValue, endValue } = g
        const time = new Date(entry.ts * 1000)
        const ts = `${time.getHours().toString().padStart(2, '0')}:${time.getMinutes().toString().padStart(2, '0')}:${time.getSeconds().toString().padStart(2, '0')}`

        let displayMessage = entry.message
        if (count > 1 && startValue !== null && endValue !== null) {
          const { signature } = splitMessage(entry.message)
          displayMessage = startValue === endValue
            ? `${signature}=${endValue}`
            : `${signature}=${startValue} → ${endValue}`
        }

        return (
          <div key={i} className="log-line">
            <span className="log-ts">{ts}</span>
            <span className="log-prefix" style={{ color: PREFIX_COLORS[entry.tag] || '#A0A0B8' }}>
              {entry.tag}
            </span>
            <span className="log-message" style={{ color: `rgb(${entry.color.join(',')})` }}>
              {displayMessage}
            </span>
            {count > 1 && <span className="log-count">×{count}</span>}
          </div>
        )
      })}
    </div>
  )
}
