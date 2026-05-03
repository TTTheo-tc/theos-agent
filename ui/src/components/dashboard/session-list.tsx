
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'

interface SessionRow {
  id: string
  channel: string
  status: string
  topic: string
  message_count: number
  total_cost: number
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-green-500/20 text-green-400 border-green-500/30',
  completed: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
  failed: 'bg-red-500/20 text-red-400 border-red-500/30',
}

const CHANNEL_SHORT: Record<string, string> = {
  telegram: 'TG', discord: 'DC', whatsapp: 'WA', feishu: 'FS',
  slack: 'SK', qq: 'QQ', dingtalk: 'DT', email: 'EM', matrix: 'MX', mochat: 'MC',
}

export function SessionList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const [sessions, setSessions] = useState<SessionRow[]>([])

  useEffect(() => {
    fetch('/api/sessions?limit=30').then(r => r.json()).then(setSessions).catch(() => {})
  }, [])

  return (
    <ScrollArea className="h-full">
      <div className="space-y-1">
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            className={cn(
              'w-full text-left px-3 py-2.5 rounded-lg transition-colors',
              selectedId === s.id
                ? 'bg-slate-800 border border-green-500/30'
                : 'hover:bg-slate-800/50 border border-transparent'
            )}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] font-mono text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded">
                {CHANNEL_SHORT[s.channel] ?? s.channel}
              </span>
              <Badge variant="outline" className={cn('text-[10px] py-0', STATUS_COLORS[s.status] ?? '')}>
                {s.status}
              </Badge>
            </div>
            <p className="text-xs text-slate-300 truncate">{s.topic || s.id.slice(0, 12)}</p>
            <p className="text-[10px] text-slate-600 mt-0.5">{s.message_count} msgs</p>
          </button>
        ))}
        {sessions.length === 0 && (
          <p className="text-xs text-slate-600 text-center py-4">No sessions yet</p>
        )}
      </div>
    </ScrollArea>
  )
}
