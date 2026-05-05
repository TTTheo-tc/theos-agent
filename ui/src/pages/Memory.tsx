import { useEffect, useState } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

type KGNode = {
  id: string
  node_type: string
  title: string
  content: string
  importance: number
  created_at: string
  updated_at: string
  tags: string
}

const NODE_TYPES = ['rule', 'task', 'research', 'lesson']

function formatDate(value: string): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleDateString()
}

export default function MemoryPage() {
  const [nodeType, setNodeType] = useState('rule')
  const [nodes, setNodes] = useState<KGNode[]>([])
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<KGNode[]>([])
  const [selected, setSelected] = useState<KGNode | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/memory/nodes?type=${nodeType}&limit=50`)
      .then(r => r.json())
      .then(setNodes)
      .catch(() => setNodes([]))
      .finally(() => setLoading(false))
  }, [nodeType])

  const doSearch = () => {
    const q = query.trim()
    if (!q) {
      setSearchResults([])
      return
    }
    fetch(`/api/memory/search?q=${encodeURIComponent(q)}&limit=20`)
      .then(r => r.json())
      .then(setSearchResults)
      .catch(() => setSearchResults([]))
  }

  return (
    <>
      <Header />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-5">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Memory</h2>
            <p className="text-xs text-slate-500 mt-1">Knowledge graph nodes and semantic recall.</p>
          </div>
          <div className="flex gap-2">
            {NODE_TYPES.map(t => (
              <button
                key={t}
                onClick={() => {
                  setNodeType(t)
                  setSelected(null)
                }}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${nodeType === t ? 'bg-green-600 text-white' : 'bg-slate-900 text-slate-400 hover:text-slate-200'}`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-12 gap-5 flex-1 min-h-0">
          <section className="col-span-5 min-h-0 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Nodes</h3>
              <Badge variant="outline">{loading ? 'loading' : `${nodes.length}`}</Badge>
            </div>
            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {nodes.map(node => (
                <button
                  key={node.id}
                  onClick={() => setSelected(node)}
                  className={`w-full text-left rounded-lg border px-4 py-3 transition-colors ${selected?.id === node.id ? 'border-green-500/50 bg-slate-900' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-200 truncate">{node.title || node.id}</p>
                      <p className="text-xs text-slate-500 mt-1 line-clamp-2">{node.content}</p>
                    </div>
                    <Badge variant="outline" className="shrink-0">{Math.round((node.importance ?? 0) * 100)}%</Badge>
                  </div>
                </button>
              ))}
              {!loading && nodes.length === 0 && (
                <p className="text-sm text-slate-500">No {nodeType} nodes found.</p>
              )}
            </div>
          </section>

          <section className="col-span-4 min-h-0 flex flex-col gap-3">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Detail</h3>
            <Card className="flex-1 min-h-0 bg-slate-900/50 border-slate-800">
              {selected ? (
                <>
                  <CardHeader className="py-4 px-5">
                    <div className="flex items-center gap-2">
                      <Badge>{selected.node_type}</Badge>
                      <CardTitle className="text-sm">{selected.title || selected.id}</CardTitle>
                    </div>
                    <div className="flex gap-4 text-[11px] text-slate-500">
                      <span>Created {formatDate(selected.created_at)}</span>
                      <span>Updated {formatDate(selected.updated_at)}</span>
                    </div>
                  </CardHeader>
                  <CardContent className="px-5 pb-5 overflow-y-auto text-sm leading-6 text-slate-300 whitespace-pre-wrap">
                    {selected.content}
                  </CardContent>
                </>
              ) : (
                <CardContent className="p-5 text-sm text-slate-500">
                  Select a node.
                </CardContent>
              )}
            </Card>
          </section>

          <section className="col-span-3 min-h-0 flex flex-col gap-3">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Search</h3>
            <div className="flex gap-2">
              <input
                placeholder="Search memory..."
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doSearch()}
                className="min-w-0 flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200"
              />
              <button onClick={doSearch} className="px-3 py-2 bg-green-600 text-white rounded-lg text-sm">Go</button>
            </div>
            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {searchResults.map(result => (
                <button
                  key={result.id}
                  onClick={() => setSelected(result)}
                  className="w-full text-left rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2 hover:border-slate-700"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px]">{result.node_type}</Badge>
                    <p className="text-xs text-slate-200 truncate">{result.title}</p>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">{result.content}</p>
                </button>
              ))}
              {query && searchResults.length === 0 && (
                <p className="text-sm text-slate-500">No search results.</p>
              )}
            </div>
          </section>
        </div>
      </main>
    </>
  )
}
