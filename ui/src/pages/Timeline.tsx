import { useState, useEffect } from 'react'
import type { AgentBar } from '@/components/viz/timeline-view'
import { Header } from '@/components/layout/header'
import { SessionList } from '@/components/dashboard/session-list'
import { TimelineView } from '@/components/viz/timeline-view'

export default function TimelinePage() {
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [agents, setAgents] = useState<AgentBar[]>([])
  const [fetchedAt, setFetchedAt] = useState(0)

  useEffect(() => {
    if (!selectedSession) return
    const controller = new AbortController()
    fetch(`/api/sessions/${selectedSession}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => {
        setAgents(data.agents ?? [])
        setFetchedAt(Date.now())
      })
      .catch((err: unknown) => {
        if ((err as { name?: string } | null)?.name === 'AbortError') return
        setAgents([])
      })
    return () => controller.abort()
  }, [selectedSession])

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 grid grid-cols-12 gap-5">
        <div className="col-span-2 overflow-hidden">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Sessions</h3>
          <SessionList selectedId={selectedSession} onSelect={setSelectedSession} />
        </div>
        <div className="col-span-10 min-h-0">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Timeline</h3>
          <TimelineView agents={agents} now={fetchedAt} />
        </div>
      </main>
    </>
  )
}
