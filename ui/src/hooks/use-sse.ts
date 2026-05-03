import { useEffect, useRef, useState, useCallback } from 'react'
import type { DashboardEvent } from '@/lib/types'

export function useSSE(onEvent?: (evt: DashboardEvent) => void) {
  const [events, setEvents] = useState<DashboardEvent[]>([])
  const lastId = useRef(0)
  const onEventRef = useRef(onEvent)
  useEffect(() => {
    onEventRef.current = onEvent
  })

  useEffect(() => {
    const es = new EventSource(`/api/events?last_event_id=${lastId.current}`)

    es.onmessage = (msg) => {
      try {
        const evt: DashboardEvent = JSON.parse(msg.data)
        if (evt.id) lastId.current = evt.id
        setEvents(prev => [evt, ...prev].slice(0, 200))
        onEventRef.current?.(evt)
      } catch {}
    }

    es.onerror = () => {
      es.close()
    }

    return () => es.close()
  }, [])

  const clear = useCallback(() => setEvents([]), [])

  return { events, clear }
}
