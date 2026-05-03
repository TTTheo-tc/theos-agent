import { useMemo } from 'react'
import type { TaskState } from '@/lib/types'

/** Agent row shape as returned by the /api/sessions/:id endpoint (snake_case from SQLite). */
export interface AgentBar {
  id: string
  name: string
  task_state: TaskState
  started_at: string
  ended_at?: string
  duration_ms: number
}

const STATE_COLORS: Record<TaskState, string> = {
  PENDING: '#64748b',
  EXECUTING: '#3b82f6',
  REVIEWING: '#f59e0b',
  APPROVED: '#22c55e',
  REJECTED: '#f97316',
  EXEC_FAILED: '#ef4444',
  FAILED: '#ef4444',
}

const ROW_HEIGHT = 36
const LABEL_WIDTH = 180
const PADDING = 16

export function TimelineView({ agents, now }: { agents: AgentBar[]; now: number }) {
  const { bars, totalWidth, totalHeight } = useMemo(() => {
    if (agents.length === 0) return { bars: [], totalWidth: 600, totalHeight: 100 }

    const times = agents.flatMap(a => {
      const s = new Date(a.started_at).getTime()
      const e = a.ended_at ? new Date(a.ended_at).getTime() : now
      return [s, e]
    }).filter(t => !isNaN(t))

    if (times.length === 0) return { bars: [], totalWidth: 600, totalHeight: 100 }

    const min = Math.min(...times)
    const max = Math.max(...times)
    const range = max - min || 1
    const chartWidth = 600

    const bars = agents.map((agent, i) => {
      const start = new Date(agent.started_at).getTime()
      const end = agent.ended_at ? new Date(agent.ended_at).getTime() : now
      const x = LABEL_WIDTH + PADDING + ((start - min) / range) * chartWidth
      const width = Math.max(((end - start) / range) * chartWidth, 4)
      const y = i * ROW_HEIGHT + PADDING

      return { agent, x, y, width, color: STATE_COLORS[agent.task_state] ?? '#64748b' }
    })

    return {
      bars,
      totalWidth: LABEL_WIDTH + PADDING * 2 + chartWidth,
      totalHeight: agents.length * ROW_HEIGHT + PADDING * 2,
    }
  }, [agents, now])

  if (agents.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-600 rounded-lg border border-slate-800 bg-slate-900/30">
        Select a session to view timeline
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto rounded-lg border border-slate-800 bg-slate-900/30">
      <svg width={totalWidth} height={totalHeight} className="min-w-full">
        {bars.map(({ agent, x, y, width, color }) => (
          <g key={agent.id}>
            <text x={8} y={y + 22} fill="#94a3b8" fontSize={11} fontFamily="Fira Code, monospace">
              {(agent.name || agent.id).slice(0, 22)}
            </text>
            <rect x={x} y={y + 6} width={width} height={22} rx={4} fill={color} opacity={0.8} />
            <text x={x + 4} y={y + 21} fill="#f8fafc" fontSize={9} fontFamily="Fira Code, monospace">
              {agent.task_state} ({((agent.duration_ms ?? 0) / 1000).toFixed(1)}s)
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}
