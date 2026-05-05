import { useEffect, useMemo, useState } from 'react'
import { Header } from '@/components/layout/header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

type WikiSection = {
  title: string
  body: string
}

export default function WikiPage() {
  const [sections, setSections] = useState<WikiSection[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)

  const load = () => {
    setLoading(true)
    fetch('/api/memory/markdown')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(data => {
        setSections(data.sections || [])
        setSelectedIndex(0)
      })
      .catch(() => setSections([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sections
    return sections.filter(section =>
      section.title.toLowerCase().includes(q) || section.body.toLowerCase().includes(q)
    )
  }, [query, sections])

  const selected = filtered[selectedIndex] ?? filtered[0]

  useEffect(() => {
    if (selectedIndex >= filtered.length) setSelectedIndex(0)
  }, [filtered.length, selectedIndex])

  return (
    <>
      <Header onRefresh={load} />
      <main className="flex-1 p-6 min-h-0 flex flex-col gap-5">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Wiki</h2>
            <p className="text-xs text-slate-500 mt-1">Document-style learning notes from memory.</p>
          </div>
          <Badge variant="outline">{loading ? 'loading' : `${sections.length} docs`}</Badge>
        </div>

        <div className="grid grid-cols-12 gap-5 flex-1 min-h-0">
          <section className="col-span-3 min-h-0 flex flex-col gap-3">
            <input
              placeholder="Search wiki..."
              value={query}
              onChange={e => setQuery(e.target.value)}
              className="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200"
            />
            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {filtered.map((section, index) => (
                <button
                  key={`${section.title}-${index}`}
                  onClick={() => setSelectedIndex(index)}
                  className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${selected === section ? 'border-green-500/50 bg-slate-900' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'}`}
                >
                  <p className="text-sm text-slate-200 truncate">{section.title || 'Untitled'}</p>
                  <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">{section.body}</p>
                </button>
              ))}
              {!loading && filtered.length === 0 && (
                <p className="text-sm text-slate-500">No wiki documents found.</p>
              )}
            </div>
          </section>

          <section className="col-span-9 min-h-0">
            <Card className="h-full min-h-0 bg-slate-900/50 border-slate-800">
              {selected ? (
                <>
                  <CardHeader className="py-5 px-6 border-b border-slate-800">
                    <CardTitle className="text-base">{selected.title || 'Untitled'}</CardTitle>
                  </CardHeader>
                  <CardContent className="p-6 overflow-y-auto h-[calc(100%-73px)]">
                    <article className="max-w-4xl whitespace-pre-wrap text-sm leading-7 text-slate-300">
                      {selected.body}
                    </article>
                  </CardContent>
                </>
              ) : (
                <CardContent className="p-6 text-sm text-slate-500">
                  No document selected.
                </CardContent>
              )}
            </Card>
          </section>
        </div>
      </main>
    </>
  )
}
