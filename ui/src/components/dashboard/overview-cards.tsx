
import { useEffect, useState } from 'react'
import { Activity, MessageSquare, Timer, DollarSign } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import type { DashboardMetrics } from '@/lib/types'

export function OverviewCards() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null)

  useEffect(() => {
    fetch('/api/metrics').then(r => r.json()).then(setMetrics).catch(() => {})
    const interval = setInterval(() => {
      fetch('/api/metrics').then(r => r.json()).then(setMetrics).catch(() => {})
    }, 10000)
    return () => clearInterval(interval)
  }, [])

  const cards = [
    { label: 'Active Sessions', value: metrics?.activeSessions ?? 0, icon: Activity, color: 'text-green-400' },
    { label: 'Messages Today', value: metrics?.messagesToday ?? 0, icon: MessageSquare, color: 'text-blue-400' },
    { label: 'Avg Latency', value: `${metrics?.avgLatencyMs ?? 0}ms`, icon: Timer, color: 'text-amber-400' },
    { label: 'Cost Today', value: `$${(metrics?.costToday ?? 0).toFixed(2)}`, icon: DollarSign, color: 'text-purple-400' },
  ]

  return (
    <div className="grid grid-cols-4 gap-4">
      {cards.map(({ label, value, icon: Icon, color }) => (
        <Card key={label} className="bg-slate-900/50 border-slate-800">
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
                <p className="text-2xl font-bold text-slate-100 mt-1 font-mono">{value}</p>
              </div>
              <Icon size={20} className={color} />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
