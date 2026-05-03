
import { useEffect, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { CostSummary } from '@/lib/types'

const PIE_COLORS = ['#22c55e', '#ef4444']

export function CostCharts() {
  const [data, setData] = useState<CostSummary | null>(null)

  useEffect(() => {
    fetch('/api/metrics/cost').then(r => r.json()).then(setData).catch(() => {})
  }, [])

  if (!data) return <p className="text-sm text-slate-600 p-4">Loading cost data...</p>

  const pieData = [
    { name: 'Cache Hit', value: data.cacheHitRate },
    { name: 'Cache Miss', value: 1 - data.cacheHitRate },
  ]

  return (
    <div className="grid grid-cols-2 gap-5 h-full">
      {/* Cost Trend */}
      <Card className="bg-slate-900/50 border-slate-800 col-span-2">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-slate-300">Cost Trend (30 days)</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={250}>
            <AreaChart data={data.daily}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} />
              <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={(v: number) => `$${v}`} />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, color: '#f8fafc', fontSize: 12 }}
              />
              <Area type="monotone" dataKey="total" fill="#8b5cf6" stroke="#8b5cf6" fillOpacity={0.6} />
            </AreaChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Cache Hit Rate */}
      <Card className="bg-slate-900/50 border-slate-800">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-slate-300">Prompt Cache Hit Rate</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center">
          <ResponsiveContainer width={200} height={200}>
            <PieChart>
              <Pie data={pieData} cx="50%" cy="50%" innerRadius={60} outerRadius={80} dataKey="value">
                {pieData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i]} />)}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <p className="text-2xl font-bold text-green-400 font-mono ml-4">
            {(data.cacheHitRate * 100).toFixed(1)}%
          </p>
        </CardContent>
      </Card>

      {/* Top Sessions by Cost */}
      <Card className="bg-slate-900/50 border-slate-800">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-slate-300">Top Sessions by Cost</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {data.topSessions.map((s, i) => (
              <div key={s.sessionId} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-slate-600 w-4">{i + 1}</span>
                  <span className="text-slate-300 truncate max-w-[200px]">{s.topic || s.sessionId.slice(0, 12)}</span>
                </div>
                <span className="text-amber-400 font-mono">${s.cost.toFixed(4)}</span>
              </div>
            ))}
            {data.topSessions.length === 0 && <p className="text-slate-600 text-xs">No data yet</p>}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
