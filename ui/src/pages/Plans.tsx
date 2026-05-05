import { useEffect, useMemo, useState } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

type PlanKind = 'daily' | 'long'
type PlanStatus = 'todo' | 'doing' | 'done'

type PlanItem = {
  id: string
  kind: PlanKind
  title: string
  status: PlanStatus
  createdAt: string
}

const STORAGE_KEY = 'theos.ui.plans'
const STATUSES: PlanStatus[] = ['todo', 'doing', 'done']

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
}

function readPlans(): PlanItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function statusClass(status: PlanStatus): string {
  if (status === 'done') return 'bg-green-500/20 text-green-400 border-green-500/30'
  if (status === 'doing') return 'bg-amber-500/20 text-amber-400 border-amber-500/30'
  return 'bg-slate-500/20 text-slate-400 border-slate-500/30'
}

export default function PlansPage() {
  const [plans, setPlans] = useState<PlanItem[]>(() => readPlans())
  const [dailyTitle, setDailyTitle] = useState('')
  const [longTitle, setLongTitle] = useState('')

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(plans))
  }, [plans])

  const grouped = useMemo(() => ({
    daily: plans.filter(plan => plan.kind === 'daily'),
    long: plans.filter(plan => plan.kind === 'long'),
  }), [plans])

  const addPlan = (kind: PlanKind) => {
    const title = (kind === 'daily' ? dailyTitle : longTitle).trim()
    if (!title) return
    setPlans(prev => [
      {
        id: newId(),
        kind,
        title,
        status: 'todo',
        createdAt: new Date().toISOString(),
      },
      ...prev,
    ])
    if (kind === 'daily') setDailyTitle('')
    else setLongTitle('')
  }

  const updateStatus = (id: string, status: PlanStatus) => {
    setPlans(prev => prev.map(plan => plan.id === id ? { ...plan, status } : plan))
  }

  const removePlan = (id: string) => {
    setPlans(prev => prev.filter(plan => plan.id !== id))
  }

  const renderColumn = (
    kind: PlanKind,
    title: string,
    value: string,
    setValue: (value: string) => void,
    items: PlanItem[],
  ) => (
    <section className="min-h-0 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{title}</h3>
        <Badge variant="outline">{items.length}</Badge>
      </div>
      <div className="flex gap-2">
        <input
          value={value}
          onChange={event => setValue(event.target.value)}
          onKeyDown={event => event.key === 'Enter' && addPlan(kind)}
          placeholder={`Add ${title.toLowerCase()} plan...`}
          className="min-w-0 flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200"
        />
        <button onClick={() => addPlan(kind)} className="px-3 py-2 bg-green-600 text-white rounded-lg text-sm">Add</button>
      </div>
      <div className="flex-1 overflow-y-auto space-y-2 pr-1">
        {items.map(item => (
          <Card key={item.id} className="bg-slate-900/50 border-slate-800">
            <CardHeader className="py-3 px-4">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-sm leading-5">{item.title}</CardTitle>
                <button onClick={() => removePlan(item.id)} className="text-xs text-slate-500 hover:text-red-400">Remove</button>
              </div>
            </CardHeader>
            <CardContent className="px-4 pb-3 flex items-center gap-2">
              {STATUSES.map(status => (
                <button
                  key={status}
                  onClick={() => updateStatus(item.id, status)}
                  className={`rounded-full border px-2 py-0.5 text-[11px] ${item.status === status ? statusClass(status) : 'border-slate-800 text-slate-500 hover:text-slate-300'}`}
                >
                  {status}
                </button>
              ))}
            </CardContent>
          </Card>
        ))}
        {items.length === 0 && <p className="text-sm text-slate-500">No {title.toLowerCase()} plans.</p>}
      </div>
    </section>
  )

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-5">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Plans</h2>
          <p className="text-xs text-slate-500 mt-1">Daily focus and long-term direction.</p>
        </div>
        <div className="grid grid-cols-2 gap-5 flex-1 min-h-0">
          {renderColumn('daily', 'Daily', dailyTitle, setDailyTitle, grouped.daily)}
          {renderColumn('long', 'Long Term', longTitle, setLongTitle, grouped.long)}
        </div>
      </main>
    </>
  )
}
