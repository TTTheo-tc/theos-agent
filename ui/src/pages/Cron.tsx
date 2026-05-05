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
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1180px] px-10 py-10">
        <div className="flex items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-medium text-[#86868b]">Cron</p>
            <h2 className="mt-2 text-[40px] font-semibold leading-[1.05] text-[#1d1d1f]">Scheduled work.</h2>
            <p className="mt-4 max-w-2xl text-base leading-7 text-[#6e6e73]">Recurring prompts and automation, without turning the page into a control room.</p>
          </div>
          <div className="flex items-center gap-2">
            {error && <span className="text-xs text-red-600">{error}</span>}
            <Badge variant="outline">{loading ? 'loading' : `${jobs.length} jobs`}</Badge>
          </div>
        </div>

        <div className="mt-8 grid grid-cols-1 gap-3">
          {jobs.map(job => (
            <Card key={job.id} className="border-white/70 bg-white/72 shadow-[0_14px_42px_rgba(29,29,31,0.05)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-white/88">
              <CardHeader className="py-4 px-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 min-w-0">
                    <button
                      onClick={() => toggle(job.id, !job.enabled)}
                      className={`mt-0.5 h-5 w-9 rounded-full p-0.5 transition-colors ${job.enabled ? 'bg-[#1d1d1f]' : 'bg-black/15'}`}
                      title={job.enabled ? 'Disable' : 'Enable'}
                    >
                      <div className={`h-4 w-4 rounded-full bg-white transition-transform ${job.enabled ? 'translate-x-4' : 'translate-x-0'}`} />
                    </button>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <CardTitle className="truncate text-sm text-[#1d1d1f]">{job.name}</CardTitle>
                        <Badge variant="outline">{fmtSchedule(job.schedule)}</Badge>
                      </div>
                      <p className="mt-2 truncate text-xs text-[#86868b]">{job.payload.message}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {job.state.lastStatus && <Badge variant={job.state.lastStatus === 'ok' ? 'default' : 'destructive'}>{job.state.lastStatus}</Badge>}
                    <button onClick={() => runNow(job.id)} className="text-xs font-medium text-[#0066cc] hover:text-[#004f9f]">Run</button>
                    <button onClick={() => remove(job.id)} className="text-xs font-medium text-red-600 hover:text-red-700">Delete</button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="grid grid-cols-3 gap-4 px-5 pb-4 text-xs text-[#86868b]">
                <span>Channel: {job.payload.channel || '-'}</span>
                <span>Next: {fmtTime(job.state.nextRunAtMs)}</span>
                <span>Last: {fmtTime(job.state.lastRunAtMs)}</span>
              </CardContent>
            </Card>
          ))}
        </div>

        {!loading && jobs.length === 0 && (
          <div className="surface mt-8 rounded-lg p-8 text-sm text-[#86868b]">
            No cron jobs configured.
          </div>
        )}
        </div>
      </main>
    </>
  )
}
