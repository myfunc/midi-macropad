/**
 * Leader election via BroadcastChannel.
 * Only the leader tab saves layout changes to the backend.
 * Other tabs show a warning banner.
 */
import { useState, useEffect, useRef } from 'react'

const CHANNEL_NAME = 'midi-macropad-tabs'
const TAB_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`

export function useTabLeader() {
  const [isLeader, setIsLeader] = useState(true)
  const [otherTabExists, setOtherTabExists] = useState(false)
  const channelRef = useRef<BroadcastChannel | null>(null)

  useEffect(() => {
    try {
      const ch = new BroadcastChannel(CHANNEL_NAME)
      channelRef.current = ch

      // Announce presence
      ch.postMessage({ type: 'ping', id: TAB_ID })

      ch.onmessage = (ev) => {
        const msg = ev.data
        if (msg.type === 'ping' && msg.id !== TAB_ID) {
          // Another tab exists — respond with pong
          setOtherTabExists(true)
          ch.postMessage({ type: 'pong', id: TAB_ID })
        }
        if (msg.type === 'pong' && msg.id !== TAB_ID) {
          // We got a response — we're not alone
          setOtherTabExists(true)
          // First tab to open is leader; subsequent tabs yield
          setIsLeader(false)
        }
        if (msg.type === 'close' && msg.id !== TAB_ID) {
          // Other tab closed — we become leader
          setOtherTabExists(false)
          setIsLeader(true)
        }
      }

      // Announce close on unload
      const onUnload = () => {
        ch.postMessage({ type: 'close', id: TAB_ID })
      }
      window.addEventListener('beforeunload', onUnload)

      return () => {
        window.removeEventListener('beforeunload', onUnload)
        ch.postMessage({ type: 'close', id: TAB_ID })
        ch.close()
      }
    } catch {
      // BroadcastChannel not supported — assume leader
      setIsLeader(true)
    }
  }, [])

  return { isLeader, otherTabExists, tabId: TAB_ID }
}
