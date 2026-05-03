import { useState, useEffect } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

type CronJob = {
  id: string; name: string; enabled: boolean;
  schedule: { kind: string; everyMs?: number; expr?: string };
  payload: { message: string; channel?: string };
  state: { nextRunAtMs?: number; lastRunAtMs?: number; lastStatus?: string };
}

function fmtSchedule(s: CronJob['schedule']): string {
  if (s.kind === 'every' && s.everyMs) return `every ${Math.round(s.everyMs / 60000)}m`
  if (s.kind === 'cron' && s.expr) return s.expr
  return s.kind
}

function fmtTime(ms?: number): string {
  return ms ? new Date(ms).toLocaleString() : '—'
}

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([])
  const load = () => { fetch('/api/cron/jobs').then(r => r.json()).then(setJobs).catch(() => setJobs([])) }
  useEffect(() => { load() }, [])

  const handleWrite = (p: Promise<Response>) => {
    p.then(r => { if (r.status === 503) alert('This feature requires the gateway to be running.'); else load() })
  }
  const toggle = (id: string, enabled: boolean) => {
    handleWrite(fetch(`/api/cron/jobs/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) }))
  }
  const runNow = (id: string) => { handleWrite(fetch(`/api/cron/jobs/${id}/run`, { method: 'POST' })) }
  const remove = (id: string) => { if (confirm('Delete this job?')) handleWrite(fetch(`/api/cron/jobs/${id}`, { method: 'DELETE' })) }

  return (
    <>
      <Header />
      <main className="flex-1 p-6 overflow-y-auto space-y-3">
        {jobs.map(job => (
          <Card key={job.id}>
            <CardHeader className="py-3 px-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <button onClick={() => toggle(job.id, !job.enabled)}
                    className={`w-8 h-4 rounded-full transition-colors ${job.enabled ? 'bg-green-600' : 'bg-slate-600'}`}>
                    <div className={`w-3 h-3 bg-white rounded-full transition-transform ${job.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                  </button>
                  <CardTitle className="text-sm">{job.name}</CardTitle>
                  <Badge variant="outline">{fmtSchedule(job.schedule)}</Badge>
                </div>
                <div className="flex items-center gap-2">
                  {job.state.lastStatus && <Badge variant={job.state.lastStatus === 'ok' ? 'default' : 'destructive'}>{job.state.lastStatus}</Badge>}
                  <button onClick={() => runNow(job.id)} className="text-xs text-green-400 hover:text-green-300">Run</button>
                  <button onClick={() => remove(job.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="px-4 pb-3 text-xs text-slate-500 flex gap-4">
              <span>Message: {job.payload.message.slice(0, 80)}</span>
              <span>Next: {fmtTime(job.state.nextRunAtMs)}</span>
              <span>Last: {fmtTime(job.state.lastRunAtMs)}</span>
            </CardContent>
          </Card>
        ))}
        {jobs.length === 0 && <p className="text-sm text-slate-500">No cron jobs configured.</p>}
      </main>
    </>
  )
}
