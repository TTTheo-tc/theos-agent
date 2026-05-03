
import { ScrollArea } from '@/components/ui/scroll-area'
import { useSSE } from '@/hooks/use-sse'
import { cn } from '@/lib/utils'
import type { DashboardEvent } from '@/lib/types'

const TYPE_COLORS: Record<string, string> = {
  task_created: 'text-blue-400',
  agent_started: 'text-green-400',
  agent_tool_use: 'text-slate-400',
  agent_finished: 'text-purple-400',
  message_in: 'text-cyan-400',
  message_out: 'text-emerald-400',
  state_change: 'text-amber-400',
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return ''
  }
}

function eventSummary(evt: DashboardEvent): string {
  const p = evt.payload
  switch (evt.type) {
    case 'task_created':
      return `Task: ${(p.description as string) ?? ''}`
    case 'agent_started':
      return `Agent started: ${(p.description as string) ?? ''}`
    case 'agent_tool_use':
      return `${(p.tool_name as string) ?? ''}: ${(p.tool_summary as string) ?? ''}`
    case 'agent_finished':
      return `Agent ${(p.state as string) ?? 'done'} (${(((p.duration_ms as number) ?? 0) / 1000).toFixed(1)}s)`
    case 'message_in':
      return `\u2190 ${(p.channel as string) ?? ''}: ${(p.content as string) ?? ''}`
    case 'message_out':
      return `\u2192 ${(p.channel as string) ?? ''}: ${((p.content as string) ?? '').slice(0, 60)}`
    case 'state_change':
      return `${(p.from as string) ?? ''} \u2192 ${(p.to as string) ?? ''}`
    default:
      return evt.type
  }
}

export function LogStream({ onEvent }: { onEvent?: (evt: DashboardEvent) => void }) {
  const { events } = useSSE(onEvent)

  return (
    <ScrollArea className="h-full">
      <div className="space-y-0.5 font-mono text-[11px]">
        {events.length === 0 && (
          <p className="text-slate-600 text-center py-4">Waiting for events...</p>
        )}
        {events.map((evt, i) => (
          <div key={evt.id ?? i} className="flex gap-2 py-1 px-1 hover:bg-slate-800/30 rounded">
            <span className="text-slate-600 shrink-0">{formatTime(evt.timestamp)}</span>
            <span className={cn('shrink-0 w-24 truncate', TYPE_COLORS[evt.type] ?? 'text-slate-500')}>
              {evt.type.replace(/_/g, ' ')}
            </span>
            <span className="text-slate-400 truncate">{eventSummary(evt)}</span>
          </div>
        ))}
      </div>
    </ScrollArea>
  )
}
