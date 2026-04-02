import { useEffect, useRef } from 'react'
import { useAppStore } from '../stores/useAppStore'
import type { IDockviewPanelProps } from 'dockview-react'

const PREFIX_COLORS: Record<string, string> = {
  PAD: '#5AE68C', KNOB: '#B482FF', OBS: '#FFA54F',
  SYS: '#64B4FF', VM: '#5AE68C', MIDI: '#64B4FF',
  WS: '#64B4FF', ERR: '#FF7878',
}

export function LogPanel(_props: IDockviewPanelProps) {
  const logs = useAppStore(s => s.logs)
  const containerRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)

  useEffect(() => {
    if (autoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [logs])

  function onScroll() {
    const el = containerRef.current
    if (!el) return
    autoScrollRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 30
  }

  return (
    <div className="log-container" ref={containerRef} onScroll={onScroll}>
      {logs.map((entry, i) => {
        const time = new Date(entry.ts * 1000)
        const ts = `${time.getHours().toString().padStart(2, '0')}:${time.getMinutes().toString().padStart(2, '0')}:${time.getSeconds().toString().padStart(2, '0')}`

        return (
          <div key={i} className="log-line">
            <span className="log-ts">{ts}</span>
            <span className="log-prefix" style={{ color: PREFIX_COLORS[entry.tag] || '#A0A0B8' }}>
              {entry.tag}
            </span>
            <span className="log-message" style={{ color: `rgb(${entry.color.join(',')})` }}>
              {entry.message}
            </span>
          </div>
        )
      })}
    </div>
  )
}
