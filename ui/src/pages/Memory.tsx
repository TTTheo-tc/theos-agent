import { useState, useEffect } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

type KGNode = {
  id: string; node_type: string; title: string; content: string;
  importance: number; created_at: string; updated_at: string; tags: string;
}
type MemorySection = { title: string; body: string }

export default function MemoryPage() {
  const [tab, setTab] = useState<'nodes' | 'search' | 'markdown'>('nodes')
  const [nodeType, setNodeType] = useState('rule')
  const [nodes, setNodes] = useState<KGNode[]>([])
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<KGNode[]>([])
  const [sections, setSections] = useState<MemorySection[]>([])
  const [selected, setSelected] = useState<KGNode | null>(null)

  useEffect(() => {
    fetch(`/api/memory/nodes?type=${nodeType}&limit=50`)
      .then(r => r.json()).then(setNodes).catch(() => setNodes([]))
  }, [nodeType])

  useEffect(() => {
    if (tab === 'markdown') {
      fetch('/api/memory/markdown')
        .then(r => r.json()).then(d => setSections(d.sections || []))
        .catch(() => setSections([]))
    }
  }, [tab])

  const doSearch = () => {
    if (!query) return
    fetch(`/api/memory/search?q=${encodeURIComponent(query)}&limit=20`)
      .then(r => r.json()).then(setSearchResults).catch(() => setSearchResults([]))
  }

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-4">
        <div className="flex gap-2">
          {(['nodes', 'search', 'markdown'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t ? 'bg-slate-800 text-green-400' : 'text-slate-400 hover:text-slate-200'}`}>{t.charAt(0).toUpperCase() + t.slice(1)}</button>
          ))}
        </div>

        {tab === 'nodes' && (
          <div className="flex-1 min-h-0 flex flex-col gap-3">
            <div className="flex gap-2">
              {['rule', 'task', 'research', 'lesson'].map(t => (
                <button key={t} onClick={() => setNodeType(t)}
                  className={`px-2 py-1 rounded text-xs ${nodeType === t ? 'bg-green-600 text-white' : 'bg-slate-800 text-slate-400'}`}>{t}</button>
              ))}
            </div>
            <div className="flex-1 overflow-y-auto space-y-2">
              {nodes.map(n => (
                <Card key={n.id} className="cursor-pointer hover:border-green-600/50" onClick={() => setSelected(selected?.id === n.id ? null : n)}>
                  <CardHeader className="py-3 px-4">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">{n.title}</CardTitle>
                      <Badge variant="outline">{(n.importance * 100).toFixed(0)}%</Badge>
                    </div>
                  </CardHeader>
                  {selected?.id === n.id && (
                    <CardContent className="px-4 pb-3 text-xs text-slate-400 whitespace-pre-wrap">{n.content}</CardContent>
                  )}
                </Card>
              ))}
              {nodes.length === 0 && <p className="text-sm text-slate-500">No {nodeType} nodes found.</p>}
            </div>
          </div>
        )}

        {tab === 'search' && (
          <div className="flex-1 min-h-0 flex flex-col gap-3">
            <div className="flex gap-2">
              <input placeholder="Search knowledge graph..." value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doSearch()}
                className="flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200" />
              <button onClick={doSearch} className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm">Search</button>
            </div>
            <div className="flex-1 overflow-y-auto space-y-2">
              {searchResults.map(n => (
                <Card key={n.id}>
                  <CardHeader className="py-3 px-4">
                    <div className="flex items-center gap-2">
                      <Badge>{n.node_type}</Badge>
                      <CardTitle className="text-sm">{n.title}</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent className="px-4 pb-3 text-xs text-slate-400">{n.content?.slice(0, 200)}</CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}

        {tab === 'markdown' && (
          <div className="flex-1 overflow-y-auto space-y-4">
            {sections.map((s, i) => (
              <Card key={i}>
                <CardHeader className="py-3 px-4"><CardTitle className="text-sm">{s.title}</CardTitle></CardHeader>
                <CardContent className="px-4 pb-3 text-xs text-slate-300 whitespace-pre-wrap">{s.body}</CardContent>
              </Card>
            ))}
          </div>
        )}
      </main>
    </>
  )
}
