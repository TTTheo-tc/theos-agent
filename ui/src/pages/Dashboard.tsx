import { useState, useEffect } from 'react'
import { Header } from '@/components/layout/header'
import { OverviewCards } from '@/components/dashboard/overview-cards'
import { SessionList } from '@/components/dashboard/session-list'
import { AgentCards } from '@/components/dashboard/agent-cards'
import { DAGView } from '@/components/viz/dag-view'
import { LogStream } from '@/components/dashboard/log-stream'
import type { Agent, TaskState } from '@/lib/types'

/** Transform raw API agent rows (snake_case) into typed Agent objects. */
function toAgents(raw: Record<string, unknown>[]): Agent[] {
  return raw.map((r) => ({
    id: String(r.id ?? ''),
    sessionId: String(r.session_id ?? ''),
    name: String(r.name ?? ''),
    status: (r.status as Agent['status']) ?? 'pending',
    taskState: (r.task_state as TaskState) ?? 'PENDING',
    model: String(r.model ?? ''),
    provider: String(r.provider ?? ''),
    startedAt: String(r.started_at ?? ''),
    endedAt: r.ended_at ? String(r.ended_at) : undefined,
    durationMs: Number(r.duration_ms ?? 0),
    retryCount: Number(r.retry_count ?? 0),
    inputTokens: Number(r.input_tokens ?? 0),
    outputTokens: Number(r.output_tokens ?? 0),
    cacheHitTokens: Number(r.cache_hit_tokens ?? 0),
    cost: {
      inputCost: 0,
      outputCost: 0,
      cacheCost: 0,
      totalCost: Number(r.cost ?? 0),
    },
    tools: [],
    children: [],
  }))
}

export default function DashboardPage() {
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [agents, setAgents] = useState<Agent[]>([])

  useEffect(() => {
    if (!selectedSession) {
      return
    }
    const controller = new AbortController()
    fetch(`/api/sessions/${selectedSession}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => setAgents(toAgents(data.agents ?? [])))
      .catch((err: unknown) => {
        if ((err as { name?: string } | null)?.name === 'AbortError') return
        setAgents([])
      })

    return () => controller.abort()
  }, [selectedSession])

  const graphAgents = selectedSession ? agents : []

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-5">
        <OverviewCards />
        <div className="flex-1 grid grid-cols-12 gap-5 min-h-0">
          <div className="col-span-2 overflow-hidden">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              Sessions
            </h3>
            <SessionList
              selectedId={selectedSession}
              onSelect={setSelectedSession}
            />
          </div>
          <div className="col-span-3 overflow-hidden">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              Agents
            </h3>
            <AgentCards sessionId={selectedSession} />
          </div>
          <div className="col-span-4 min-h-0">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              Call Graph
            </h3>
            <DAGView agents={graphAgents} />
          </div>
          <div className="col-span-3 min-h-0">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              Live Events
            </h3>
            <LogStream />
          </div>
        </div>
      </main>
    </>
  )
}
