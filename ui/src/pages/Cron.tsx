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
  return ms ? new Date(ms).toLocaleString() : '-'
}

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    fetch('/api/cron/jobs')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(setJobs)
      .catch(() => {
        setJobs([])
        setError('Cron data unavailable.')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleWrite = (p: Promise<Response>) => {
    p.then(r => {
      if (r.status === 503) setError('This action requires the gateway.')
      else load()
    }).catch(() => setError('Cron action failed.'))
  }
  const toggle = (id: string, enabled: boolean) => {
    handleWrite(fetch(`/api/cron/jobs/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) }))
  }
  const runNow = (id: string) => { handleWrite(fetch(`/api/cron/jobs/${id}/run`, { method: 'POST' })) }
  const remove = (id: string) => { if (confirm('Delete this job?')) handleWrite(fetch(`/api/cron/jobs/${id}`, { method: 'DELETE' })) }

  return (
    <>
      <Header onRefresh={load} />
      <main className="flex-1 p-6 overflow-y-auto space-y-5">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Cron</h2>
            <p className="text-xs text-slate-500 mt-1">Recurring work and scheduled prompts.</p>
          </div>
          <div className="flex items-center gap-2">
            {error && <span className="text-xs text-red-400">{error}</span>}
            <Badge variant="outline">{loading ? 'loading' : `${jobs.length} jobs`}</Badge>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3">
          {jobs.map(job => (
            <Card key={job.id} className="bg-slate-900/50 border-slate-800">
              <CardHeader className="py-4 px-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 min-w-0">
                    <button
                      onClick={() => toggle(job.id, !job.enabled)}
                      className={`mt-0.5 w-9 h-5 rounded-full transition-colors ${job.enabled ? 'bg-green-600' : 'bg-slate-700'}`}
                      title={job.enabled ? 'Disable' : 'Enable'}
                    >
                      <div className={`w-4 h-4 bg-white rounded-full transition-transform ${job.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <CardTitle className="text-sm truncate">{job.name}</CardTitle>
                        <Badge variant="outline">{fmtSchedule(job.schedule)}</Badge>
                      </div>
                      <p className="text-xs text-slate-500 mt-2 truncate">{job.payload.message}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {job.state.lastStatus && <Badge variant={job.state.lastStatus === 'ok' ? 'default' : 'destructive'}>{job.state.lastStatus}</Badge>}
                    <button onClick={() => runNow(job.id)} className="text-xs text-green-400 hover:text-green-300">Run</button>
                    <button onClick={() => remove(job.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="px-5 pb-4 text-xs text-slate-500 grid grid-cols-3 gap-4">
                <span>Channel: {job.payload.channel || '-'}</span>
                <span>Next: {fmtTime(job.state.nextRunAtMs)}</span>
                <span>Last: {fmtTime(job.state.lastRunAtMs)}</span>
              </CardContent>
            </Card>
          ))}
        </div>

        {!loading && jobs.length === 0 && (
          <p className="text-sm text-slate-500">No cron jobs configured.</p>
        )}
      </main>
    </>
  )
}
