import { useState, useEffect } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

type ToolInfo = { name: string; description: string; risk_level: string; owner_only: boolean; schema: Record<string, unknown> }
type ToolsResponse = { tools?: ToolInfo[]; profiles?: Record<string, string[] | null>; groups?: Record<string, string[]>; mode: string }

const RISK_COLORS: Record<string, string> = { low: 'bg-green-600', medium: 'bg-amber-600', high: 'bg-red-600' }

export default function ToolsPage() {
  const [data, setData] = useState<ToolsResponse | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => { fetch('/api/tools').then(r => r.json()).then(setData).catch(() => null) }, [])

  if (!data) return <><Header /><main className="flex-1 p-6"><p className="text-sm text-slate-500">Loading...</p></main></>

  return (
    <>
      <Header />
      <main className="flex-1 p-6 overflow-y-auto space-y-4">
        {data.mode === 'static' && <Badge variant="outline" className="text-xs">Static mode — start gateway for full tool details</Badge>}
        {data.tools && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.tools.map(tool => (
              <Card key={tool.name} className="cursor-pointer hover:border-green-600/50" onClick={() => setExpanded(expanded === tool.name ? null : tool.name)}>
                <CardHeader className="py-3 px-4">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm font-mono">{tool.name}</CardTitle>
                    <div className="flex gap-1">
                      <Badge className={RISK_COLORS[tool.risk_level] || 'bg-slate-600'}>{tool.risk_level}</Badge>
                      {tool.owner_only && <Badge variant="destructive">owner</Badge>}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="px-4 pb-3 text-xs text-slate-400">
                  {tool.description?.slice(0, 100)}
                  {expanded === tool.name && (
                    <pre className="mt-2 p-2 bg-slate-950 rounded text-[10px] overflow-x-auto">{JSON.stringify(tool.schema, null, 2)}</pre>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
        {data.profiles && !data.tools && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-300">Profiles</h3>
            {Object.entries(data.profiles).map(([name, tools]) => (
              <Card key={name}>
                <CardHeader className="py-2 px-4"><CardTitle className="text-sm">{name}</CardTitle></CardHeader>
                <CardContent className="px-4 pb-3 text-xs text-slate-500">{tools ? tools.join(', ') : 'All tools'}</CardContent>
              </Card>
            ))}
          </div>
        )}
      </main>
    </>
  )
}
