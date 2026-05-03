import { useState, useEffect, useCallback } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

type Settings = {
  theme: string; refresh_interval_ms: number; logs_auto_scroll: boolean;
  logs_default_level: string; sidebar_collapsed: boolean;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  useEffect(() => { fetch('/api/settings').then(r => r.json()).then(setSettings).catch(() => null) }, [])

  const update = useCallback((patch: Partial<Settings>) => {
    setSettings(prev => prev ? { ...prev, ...patch } : null)
    fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
  }, [])

  if (!settings) return <><Header /><main className="flex-1 p-6"><p className="text-sm text-slate-500">Loading...</p></main></>

  return (
    <>
      <Header />
      <main className="flex-1 p-6 overflow-y-auto space-y-6">
        <Card>
          <CardHeader><CardTitle className="text-sm">Appearance</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Theme</span>
              <select value={settings.theme} onChange={e => update({ theme: e.target.value })}
                className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200">
                <option value="dark">Dark</option>
                <option value="light">Light</option>
              </select>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">Dashboard</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Refresh interval</span>
              <select value={String(settings.refresh_interval_ms)} onChange={e => update({ refresh_interval_ms: Number(e.target.value) })}
                className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200">
                <option value="5000">5s</option>
                <option value="10000">10s</option>
                <option value="30000">30s</option>
              </select>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Collapse sidebar</span>
              <input type="checkbox" checked={settings.sidebar_collapsed} onChange={e => update({ sidebar_collapsed: e.target.checked })} />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">Logs</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Auto-scroll</span>
              <input type="checkbox" checked={settings.logs_auto_scroll} onChange={e => update({ logs_auto_scroll: e.target.checked })} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Default level</span>
              <select value={settings.logs_default_level} onChange={e => update({ logs_default_level: e.target.value })}
                className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200">
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  )
}
