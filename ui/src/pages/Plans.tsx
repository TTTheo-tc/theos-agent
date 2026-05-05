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
  if (status === 'done') return 'bg-[#1d1d1f] text-white border-[#1d1d1f]'
  if (status === 'doing') return 'bg-[#e8f2ff] text-[#0066cc] border-[#cfe4ff]'
  return 'bg-white/70 text-[#6e6e73] border-white/80'
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
    <section className="surface min-h-0 flex flex-col gap-4 rounded-lg p-6">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[#1d1d1f]">{title}</h3>
        <Badge variant="outline">{items.length}</Badge>
      </div>
      <div className="flex gap-2">
        <input
          value={value}
          onChange={event => setValue(event.target.value)}
          onKeyDown={event => event.key === 'Enter' && addPlan(kind)}
          placeholder={`Add ${title.toLowerCase()} plan...`}
          className="soft-input min-w-0 flex-1 rounded-lg px-3 py-2 text-sm text-[#1d1d1f] outline-none transition-colors placeholder:text-[#86868b] focus:border-[#0071e3]"
        />
        <button onClick={() => addPlan(kind)} className="rounded-lg bg-[#1d1d1f] px-4 py-2 text-sm font-medium text-white">Add</button>
      </div>
      <div className="flex-1 overflow-y-auto space-y-2 pr-1">
        {items.map(item => (
          <Card key={item.id} className="border-white/70 bg-white/62 shadow-[0_12px_34px_rgba(29,29,31,0.045)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-white/82">
            <CardHeader className="px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-sm leading-5 text-[#1d1d1f]">{item.title}</CardTitle>
                <button onClick={() => removePlan(item.id)} className="text-xs text-[#86868b] hover:text-red-600">Remove</button>
              </div>
            </CardHeader>
            <CardContent className="px-4 pb-3 flex items-center gap-2">
              {STATUSES.map(status => (
                <button
                  key={status}
                  onClick={() => updateStatus(item.id, status)}
                  className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${item.status === status ? statusClass(status) : 'border-white/80 bg-white/45 text-[#86868b] hover:text-[#1d1d1f]'}`}
                >
                  {status}
                </button>
              ))}
            </CardContent>
          </Card>
        ))}
        {items.length === 0 && <p className="pt-4 text-sm text-[#86868b]">No {title.toLowerCase()} plans.</p>}
      </div>
    </section>
  )

  return (
    <>
      <Header />
      <main className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto flex h-full w-full max-w-[1180px] flex-col gap-8 px-10 py-10">
        <div className="max-w-3xl">
          <p className="text-sm font-medium text-[#86868b]">Plans</p>
          <h2 className="mt-2 text-[40px] font-semibold leading-[1.05] text-[#1d1d1f]">Today and direction.</h2>
          <p className="mt-4 max-w-2xl text-base leading-7 text-[#6e6e73]">Keep the daily list small, and keep long-term work visible.</p>
        </div>
        <div className="grid grid-cols-2 gap-5 flex-1 min-h-0">
          {renderColumn('daily', 'Daily', dailyTitle, setDailyTitle, grouped.daily)}
          {renderColumn('long', 'Long Term', longTitle, setLongTitle, grouped.long)}
        </div>
        </div>
      </main>
    </>
  )
}
