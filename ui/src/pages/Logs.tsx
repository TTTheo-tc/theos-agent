import { useState, useEffect, useRef } from 'react'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'

type LogEntry = { level: string; message: string; timestamp: string; logger: string }

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'bg-slate-600', INFO: 'bg-blue-600', WARNING: 'bg-amber-600', ERROR: 'bg-red-600',
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [level, setLevel] = useState('ALL')
  const [query, setQuery] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const params = new URLSearchParams()
    if (level !== 'ALL') params.set('level', level)
    if (query) params.set('q', query)
    params.set('limit', '500')
    fetch(`/api/logs?${params}`).then(r => r.json()).then(setLogs).catch(() => setLogs([]))
  }, [level, query])

  useEffect(() => {
    const es = new EventSource('/api/logs/stream')
    es.onmessage = (e) => {
      try {
        const entry = JSON.parse(e.data) as LogEntry
        if (level !== 'ALL' && entry.level !== level) return
        setLogs(prev => [...prev.slice(-499), entry])
      } catch {}
    }
    return () => es.close()
  }, [level])

  useEffect(() => {
    if (autoScroll) endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs, autoScroll])

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <select value={level} onChange={e => setLevel(e.target.value)}
            className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200">
            <option value="ALL">All Levels</option>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
          <input placeholder="Filter logs..." value={query} onChange={e => setQuery(e.target.value)}
            className="flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200" />
          <label className="flex items-center gap-2 text-xs text-slate-500">
            <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} /> Auto-scroll
          </label>
        </div>
        <div className="flex-1 overflow-y-auto font-mono text-xs space-y-0.5 bg-slate-950 rounded-lg p-3">
          {logs.map((entry, i) => (
            <div key={i} className="flex gap-2 hover:bg-slate-800/50 px-1 py-0.5 rounded">
              <span className="text-slate-600 shrink-0 w-44">{entry.timestamp?.slice(0, 23)}</span>
              <Badge className={`${LEVEL_COLORS[entry.level] || 'bg-slate-600'} text-[10px] h-4 shrink-0`}>{entry.level}</Badge>
              <span className="text-slate-300 break-all">{entry.message}</span>
            </div>
          ))}
          <div ref={endRef} />
          {logs.length === 0 && <p className="text-slate-500">No logs available.</p>}
        </div>
      </main>
    </>
  )
}
