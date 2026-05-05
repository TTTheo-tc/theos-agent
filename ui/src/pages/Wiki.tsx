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
      <main className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto flex h-full w-full max-w-[1180px] flex-col gap-8 px-10 py-10">
        <div className="flex items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-medium text-[#86868b]">Wiki</p>
            <h2 className="mt-2 text-[40px] font-semibold leading-[1.05] text-[#1d1d1f]">Learning notes.</h2>
            <p className="mt-4 max-w-2xl text-base leading-7 text-[#6e6e73]">Document-style knowledge, kept readable before it becomes searchable.</p>
          </div>
          <Badge variant="outline">{loading ? 'loading' : `${sections.length} docs`}</Badge>
        </div>

        <div className="grid grid-cols-12 gap-5 flex-1 min-h-0">
          <section className="col-span-3 min-h-0 flex flex-col gap-3">
            <input
              placeholder="Search wiki..."
              value={query}
              onChange={e => setQuery(e.target.value)}
              className="soft-input rounded-lg px-3 py-2 text-sm text-[#1d1d1f] outline-none transition-colors placeholder:text-[#86868b] focus:border-[#0071e3]"
            />
            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {filtered.map((section, index) => (
                <button
                  key={`${section.title}-${index}`}
                  onClick={() => setSelectedIndex(index)}
                  className={`w-full rounded-lg px-3 py-3 text-left transition-all duration-200 ${selected === section ? 'bg-[#1d1d1f] text-white shadow-[0_14px_34px_rgba(29,29,31,0.18)]' : 'bg-white/55 text-[#1d1d1f] hover:-translate-y-0.5 hover:bg-white/82 hover:shadow-[0_12px_30px_rgba(29,29,31,0.055)]'}`}
                >
                  <p className="truncate text-sm font-medium">{section.title || 'Untitled'}</p>
                  <p className={`mt-1 line-clamp-2 text-[11px] ${selected === section ? 'text-white/60' : 'text-[#86868b]'}`}>{section.body}</p>
                </button>
              ))}
              {!loading && filtered.length === 0 && (
                <p className="text-sm text-[#86868b]">No wiki documents found.</p>
              )}
            </div>
          </section>

          <section className="col-span-9 min-h-0">
            <Card className="surface h-full min-h-0">
              {selected ? (
                <>
                  <CardHeader className="px-8 py-6 shadow-[inset_0_-1px_rgba(29,29,31,0.045)]">
                    <CardTitle className="text-2xl text-[#1d1d1f]">{selected.title || 'Untitled'}</CardTitle>
                  </CardHeader>
                  <CardContent className="h-[calc(100%-89px)] overflow-y-auto p-8">
                    <article className="max-w-3xl whitespace-pre-wrap text-[15px] leading-8 text-[#515154]">
                      {selected.body}
                    </article>
                  </CardContent>
                </>
              ) : (
                <CardContent className="p-8 text-sm text-[#86868b]">
                  No document selected.
                </CardContent>
              )}
            </Card>
          </section>
        </div>
        </div>
      </main>
    </>
  )
}
