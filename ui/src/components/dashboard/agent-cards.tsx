import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, RotateCcw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

type TaskState = 'PENDING' | 'EXECUTING' | 'REVIEWING' | 'APPROVED' | 'REJECTED' | 'EXEC_FAILED' | 'FAILED'

interface AgentRow {
  id: string
  name: string
  status: string
  task_state: TaskState
  model: string
  started_at: string
  duration_ms: number
  retry_count: number
  input_tokens: number
  output_tokens: number
  cache_hit_tokens: number
  cost: number
  data: string
}

const STATE_COLORS: Record<TaskState, string> = {
  PENDING: 'bg-slate-500/20 text-slate-400',
  EXECUTING: 'bg-blue-500/20 text-blue-400',
  REVIEWING: 'bg-amber-500/20 text-amber-400',
  APPROVED: 'bg-green-500/20 text-green-400',
  REJECTED: 'bg-orange-500/20 text-orange-400',
  EXEC_FAILED: 'bg-red-500/20 text-red-400',
  FAILED: 'bg-red-500/20 text-red-400',
}

const STATUS_DOT: Record<string, string> = {
  pending: 'bg-slate-500',
  running: 'bg-green-500 animate-pulse',
  completed: 'bg-green-500',
  failed: 'bg-red-500',
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function AgentCards({ sessionId }: { sessionId: string | null }) {
  const [agents, setAgents] = useState<AgentRow[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!sessionId) return
    const controller = new AbortController()
    fetch(`/api/sessions/${sessionId}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { agents?: AgentRow[] }) => setAgents(data.agents ?? []))
      .catch((err: unknown) => {
        if ((err as { name?: string } | null)?.name === 'AbortError') return
        setAgents([])
      })
    return () => controller.abort()
  }, [sessionId])

  const toggle = (id: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (!sessionId) return <p className="text-sm text-slate-600">Select a session</p>
  if (agents.length === 0) return <p className="text-sm text-slate-600">No agents in this session</p>

  return (
    <ScrollArea className="h-full">
      <div className="space-y-2">
        {agents.map((agent) => {
          const isOpen = expanded.has(agent.id)
          return (
            <div key={agent.id} className="rounded-lg border border-slate-800 bg-slate-900/50 overflow-hidden">
              <button
                onClick={() => toggle(agent.id)}
                className="w-full px-3 py-2.5 flex items-center gap-2 hover:bg-slate-800/50 transition-colors text-left"
              >
                {isOpen ? <ChevronDown size={12} className="text-slate-500" /> : <ChevronRight size={12} className="text-slate-500" />}
                <div className={cn('w-2 h-2 rounded-full shrink-0', STATUS_DOT[agent.status] ?? 'bg-slate-500')} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-slate-200 truncate">{agent.name || agent.id.slice(0, 12)}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <Badge variant="outline" className={cn('text-[9px] py-0', STATE_COLORS[agent.task_state] ?? '')}>
                      {agent.task_state}
                    </Badge>
                    <span className="text-[10px] text-slate-600 font-mono">
                      {formatTokens(agent.input_tokens + agent.output_tokens)} tok
                    </span>
                    {agent.retry_count > 0 && (
                      <span className="flex items-center gap-0.5 text-[10px] text-amber-500">
                        <RotateCcw size={8} /> {agent.retry_count}
                      </span>
                    )}
                  </div>
                </div>
                <span className="text-[10px] text-slate-600 font-mono shrink-0">
                  ${(agent.cost ?? 0).toFixed(4)}
                </span>
              </button>
              {isOpen && (
                <div className="px-3 pb-3 border-t border-slate-800 pt-2 text-xs space-y-2">
                  <div className="grid grid-cols-3 gap-2">
                    <div><span className="text-slate-500">Model</span><p className="text-slate-300 font-mono text-[10px]">{agent.model || '-'}</p></div>
                    <div><span className="text-slate-500">Duration</span><p className="text-slate-300 font-mono text-[10px]">{((agent.duration_ms ?? 0) / 1000).toFixed(1)}s</p></div>
                    <div><span className="text-slate-500">Cache Hit</span><p className="text-slate-300 font-mono text-[10px]">{formatTokens(agent.cache_hit_tokens ?? 0)}</p></div>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </ScrollArea>
  )
}
