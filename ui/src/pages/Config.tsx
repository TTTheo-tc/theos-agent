import { useState, useEffect } from 'react'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'

export default function ConfigPage() {
  const [config, setConfig] = useState('')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetch('/api/config').then(r => r.json())
      .then(data => setConfig(JSON.stringify(data, null, 2)))
      .catch(() => setConfig('{}'))
  }, [])

  const save = () => {
    setError(''); setSaved(false)
    try {
      const parsed = JSON.parse(config)
      fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parsed) })
        .then(r => { if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 3000) } else r.json().then(d => setError(d.error || 'Save failed')) })
    } catch { setError('Invalid JSON') }
  }

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-300">config.json</h2>
            <Badge variant="outline" className="text-[10px]">Secrets redacted as ***</Badge>
          </div>
          <div className="flex items-center gap-2">
            {error && <span className="text-xs text-red-400">{error}</span>}
            {saved && <span className="text-xs text-green-400">Saved!</span>}
            <button onClick={save} className="px-4 py-1.5 bg-green-600 text-white rounded-lg text-sm hover:bg-green-500">Save</button>
          </div>
        </div>
        <textarea value={config} onChange={e => setConfig(e.target.value)}
          className="flex-1 font-mono text-xs bg-slate-950 border border-slate-700 rounded-lg p-3 text-slate-200 resize-none" />
      </main>
    </>
  )
}
