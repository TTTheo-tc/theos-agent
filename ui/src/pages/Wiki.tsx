import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookOpenText, FileText, FolderOpen, Layers3, Plus, Save, Search, type LucideIcon } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'
import { useDelayedSync } from '@/lib/use-delayed-sync'

type WikiFile = {
  path: string
  name: string
  title: string
  summary: string
  category: string
  size: number
  updatedAt: string
  snippet?: string
}

type WikiLogEntry = {
  date: string
  kind: string
  title: string
}

type WikiStatus = {
  root: string
  initialized: boolean
  counts: Record<string, number>
  files: WikiFile[]
  log: WikiLogEntry[]
  indexPreview: string
  schemaPath?: string
  error?: string
}

type RecordCategory = 'sources' | 'concepts' | 'entities' | 'outputs'

type DraftRecord = {
  category: RecordCategory
  title: string
  summary: string
  body: string
  tags: string
  sources: string
}

const GROUP_ORDER = ['base', 'sources', 'concepts', 'entities', 'outputs', 'raw', 'schema']
const GROUP_LABELS: Record<string, string> = {
  base: 'Index',
  sources: 'Sources',
  concepts: 'Concepts',
  entities: 'Entities',
  outputs: 'Outputs',
  raw: 'Raw',
  schema: 'Schema',
}

const WORKFLOWS = [
  { label: 'Ingest', text: 'raw -> sources / concepts' },
  { label: 'Query', text: 'index -> pages -> output' },
  { label: 'Lint', text: 'links / conflicts / gaps' },
]

const RECORD_CATEGORIES: Array<{ value: RecordCategory; label: string }> = [
  { value: 'sources', label: 'Source' },
  { value: 'concepts', label: 'Concept' },
  { value: 'entities', label: 'Entity' },
  { value: 'outputs', label: 'Output' },
]

const EMPTY_RECORD: DraftRecord = {
  category: 'sources',
  title: '',
  summary: '',
  body: '',
  tags: '',
  sources: '',
}

function chooseDefault(files: WikiFile[]): string {
  return files.find(file => file.path === 'wiki/index.md')?.path ?? files[0]?.path ?? ''
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleDateString()
}

function count(status: WikiStatus | null, key: string): string {
  return String(status?.counts?.[key] ?? 0)
}

export default function WikiPage() {
  const [status, setStatus] = useState<WikiStatus | null>(null)
  const [selectedPath, setSelectedPath] = useState('')
  const [content, setContent] = useState('')
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<WikiFile[]>([])
  const [loading, setLoading] = useState(false)
  const [pageLoading, setPageLoading] = useState(false)
  const [initializing, setInitializing] = useState(false)
  const [record, setRecord] = useState<DraftRecord>(EMPTY_RECORD)
  const [savingRecord, setSavingRecord] = useState(false)
  const [recordError, setRecordError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    return fetch('/api/wiki/status')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((data: WikiStatus) => {
        setStatus(data)
        setSelectedPath(current => {
          if (current && data.files.some(file => file.path === current)) return current
          return chooseDefault(data.files)
        })
      })
      .catch(() => {
        setStatus(null)
        setSelectedPath('')
      })
      .finally(() => setLoading(false))
  }, [])

  useDelayedSync(load, 15000)

  const initialize = () => {
    setInitializing(true)
    fetch('/api/wiki/init', { method: 'POST' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((data: WikiStatus) => {
        setStatus(data)
        setSelectedPath(chooseDefault(data.files))
      })
      .finally(() => setInitializing(false))
  }

  const updateRecord = (patch: Partial<DraftRecord>) => {
    setRecord(current => ({ ...current, ...patch }))
  }

  const saveRecord = () => {
    if (!record.title.trim()) return
    setSavingRecord(true)
    setRecordError('')
    fetch('/api/wiki/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(record),
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((data: { file: WikiFile; status: WikiStatus }) => {
        setStatus(data.status)
        setSelectedPath(data.file.path)
        setQuery('')
        setSearchResults([])
        setRecord(current => ({ ...EMPTY_RECORD, category: current.category }))
      })
      .catch(() => setRecordError('Save failed.'))
      .finally(() => setSavingRecord(false))
  }

  useEffect(() => {
    if (!selectedPath) {
      setContent('')
      return
    }

    let cancelled = false
    setPageLoading(true)
    fetch(`/api/wiki/page?path=${encodeURIComponent(selectedPath)}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(data => {
        if (!cancelled) setContent(data.content || '')
      })
      .catch(() => {
        if (!cancelled) setContent('')
      })
      .finally(() => {
        if (!cancelled) setPageLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedPath])

  useEffect(() => {
    const q = query.trim()
    if (!q) {
      setSearchResults([])
      return
    }

    let cancelled = false
    const timer = window.setTimeout(() => {
      fetch(`/api/wiki/search?q=${encodeURIComponent(q)}`)
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(data => {
          if (!cancelled) setSearchResults(data || [])
        })
        .catch(() => {
          if (!cancelled) setSearchResults([])
        })
    }, 250)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query])

  const visibleFiles = query.trim() ? searchResults : status?.files ?? []
  const selectedFile = status?.files.find(file => file.path === selectedPath)

  const groupedFiles = useMemo(() => {
    const groups = new Map<string, WikiFile[]>()
    for (const file of visibleFiles) {
      const key = file.category || 'base'
      groups.set(key, [...(groups.get(key) ?? []), file])
    }
    return [...GROUP_ORDER, ...Array.from(groups.keys()).filter(key => !GROUP_ORDER.includes(key))]
      .filter(key => groups.has(key))
      .map(key => ({ key, label: GROUP_LABELS[key] ?? key, files: groups.get(key) ?? [] }))
  }, [visibleFiles])

  return (
    <>
      <Header onRefresh={load} />
      <main className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-8 px-10 py-10">
          <div className="flex items-start justify-between gap-6">
            <div className="max-w-3xl">
              <p className="text-sm font-medium text-[#86868b]">Wiki</p>
              <h2 className="mt-2 text-[42px] font-semibold leading-[1.05] text-[#1d1d1f]">Personal learning wiki.</h2>
              <p className="mt-4 max-w-2xl text-base leading-7 text-[#6e6e73]">
                Local Markdown knowledge, separated from memory and ready for raw sources, generated pages, and reusable outputs.
              </p>
            </div>
            <Badge variant="outline">{loading ? 'syncing' : status?.initialized ? `${status.files.length} files` : 'not initialized'}</Badge>
          </div>

          {!status?.initialized ? (
            <section className="grid grid-cols-12 gap-5">
              <div className="col-span-8 rounded-lg bg-[linear-gradient(135deg,#1d1d1f_0%,#2f3034_100%)] p-8 text-white shadow-[0_28px_75px_rgba(29,29,31,0.2)]">
                <div className="flex items-start justify-between gap-6">
                  <div>
                    <p className="text-sm text-white/55">LLM Wiki</p>
                    <h3 className="mt-3 text-3xl font-semibold">No workspace yet.</h3>
                    <p className="mt-3 max-w-xl text-sm leading-6 text-white/62">
                      {status?.root || status?.error || 'Workspace path unavailable.'}
                    </p>
                  </div>
                  <button
                    onClick={initialize}
                    disabled={initializing || !status?.root}
                    className="inline-flex items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-medium text-[#1d1d1f] shadow-[0_14px_34px_rgba(0,0,0,0.16)] transition-transform hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Plus size={16} />
                    {initializing ? 'Creating' : 'Create'}
                  </button>
                </div>
                <div className="mt-10 grid grid-cols-3 gap-4">
                  <Layer label="Raw" text="original material" />
                  <Layer label="Wiki" text="structured pages" />
                  <Layer label="Schema" text="CLAUDE.md" />
                </div>
              </div>
              <div className="surface col-span-4 rounded-lg p-8">
                <p className="text-sm font-medium text-[#86868b]">Workflow</p>
                <div className="mt-5 space-y-4">
                  {WORKFLOWS.map(item => (
                    <div key={item.label} className="rounded-lg bg-white/60 p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.7)]">
                      <p className="text-sm font-semibold text-[#1d1d1f]">{item.label}</p>
                      <p className="mt-1 text-xs text-[#86868b]">{item.text}</p>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          ) : (
            <>
              <section className="grid grid-cols-4 gap-4">
                <Metric icon={FolderOpen} label="Raw" value={count(status, 'raw')} />
                <Metric icon={FileText} label="Sources" value={count(status, 'sources')} />
                <Metric icon={Layers3} label="Concepts" value={count(status, 'concepts')} />
                <Metric icon={BookOpenText} label="Outputs" value={count(status, 'outputs')} />
              </section>

              <section className="grid grid-cols-12 gap-5">
                <aside className="col-span-3 min-h-[640px]">
                  <div className="surface flex h-full flex-col rounded-lg p-4">
                    <div className="soft-input flex items-center gap-2 rounded-lg px-3 py-2">
                      <Search size={15} className="text-[#86868b]" />
                      <input
                        value={query}
                        onChange={event => setQuery(event.target.value)}
                        placeholder="Search wiki..."
                        className="min-w-0 flex-1 bg-transparent text-sm text-[#1d1d1f] outline-none placeholder:text-[#86868b]"
                      />
                    </div>
                    <div className="mt-4 flex-1 overflow-y-auto pr-1">
                      {groupedFiles.map(group => (
                        <div key={group.key} className="mb-5">
                          <p className="mb-2 px-2 text-[11px] font-medium text-[#86868b]">{group.label}</p>
                          <div className="space-y-1.5">
                            {group.files.map(file => (
                              <button
                                key={file.path}
                                onClick={() => setSelectedPath(file.path)}
                                className={`w-full rounded-lg px-3 py-3 text-left transition-all duration-200 ${selectedPath === file.path ? 'bg-[#1d1d1f] text-white shadow-[0_14px_34px_rgba(29,29,31,0.18)]' : 'bg-white/48 text-[#1d1d1f] hover:-translate-y-0.5 hover:bg-white/78 hover:shadow-[0_12px_30px_rgba(29,29,31,0.055)]'}`}
                              >
                                <p className="truncate text-sm font-medium">{file.title || file.name}</p>
                                <p className={`mt-1 line-clamp-2 text-[11px] leading-4 ${selectedPath === file.path ? 'text-white/60' : 'text-[#86868b]'}`}>
                                  {file.snippet || file.summary || file.path}
                                </p>
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                      {visibleFiles.length === 0 && (
                        <p className="px-2 text-sm leading-6 text-[#86868b]">
                          {query.trim() ? 'No matching pages.' : 'No wiki pages yet.'}
                        </p>
                      )}
                    </div>
                  </div>
                </aside>

                <section className="surface col-span-6 min-h-[640px] rounded-lg">
                  <div className="flex items-start justify-between gap-4 px-8 py-6 shadow-[inset_0_-1px_rgba(29,29,31,0.045)]">
                    <div className="min-w-0">
                      <p className="truncate text-sm text-[#86868b]">{selectedPath || 'No page selected'}</p>
                      <h3 className="mt-2 truncate text-2xl font-semibold text-[#1d1d1f]">
                        {selectedFile?.title || 'Wiki'}
                      </h3>
                    </div>
                    <Badge variant="outline">{pageLoading ? 'loading' : selectedFile ? formatDate(selectedFile.updatedAt) : '-'}</Badge>
                  </div>
                  <article className="h-[calc(100%-98px)] overflow-y-auto whitespace-pre-wrap px-8 py-7 text-[15px] leading-8 text-[#515154]">
                    {content || 'No content.'}
                  </article>
                </section>

                <aside className="col-span-3 min-h-[640px] space-y-5">
                  <RecordPanel
                    draft={record}
                    disabled={savingRecord}
                    error={recordError}
                    onChange={updateRecord}
                    onSave={saveRecord}
                  />

                  <section className="surface rounded-lg p-5">
                    <p className="text-sm font-medium text-[#86868b]">Workflow</p>
                    <div className="mt-4 space-y-3">
                      {WORKFLOWS.map(item => (
                        <div key={item.label} className="rounded-lg bg-white/55 p-4">
                          <p className="text-sm font-semibold text-[#1d1d1f]">{item.label}</p>
                          <p className="mt-1 text-xs leading-5 text-[#86868b]">{item.text}</p>
                        </div>
                      ))}
                    </div>
                  </section>

                  <section className="surface rounded-lg p-5">
                    <p className="text-sm font-medium text-[#86868b]">Log</p>
                    <div className="mt-4 space-y-4">
                      {(status.log ?? []).map((entry, index) => (
                        <div key={`${entry.date}-${entry.title}-${index}`} className="shadow-[inset_0_-1px_rgba(29,29,31,0.045)] pb-4 last:shadow-none last:pb-0">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-medium text-[#1d1d1f]">{entry.kind || 'note'}</span>
                            <span className="text-[11px] text-[#86868b]">{entry.date || '-'}</span>
                          </div>
                          <p className="mt-1 text-sm leading-5 text-[#6e6e73]">{entry.title}</p>
                        </div>
                      ))}
                      {status.log.length === 0 && <p className="text-sm text-[#86868b]">No log entries.</p>}
                    </div>
                  </section>
                </aside>
              </section>
            </>
          )}
        </div>
      </main>
    </>
  )
}

function Metric({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="surface-soft rounded-lg p-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-[#86868b]">{label}</p>
        <Icon size={16} className="text-[#6e6e73]" />
      </div>
      <p className="mt-4 text-3xl font-semibold text-[#1d1d1f]">{value}</p>
    </div>
  )
}

function RecordPanel({
  draft,
  disabled,
  error,
  onChange,
  onSave,
}: {
  draft: DraftRecord
  disabled: boolean
  error: string
  onChange: (patch: Partial<DraftRecord>) => void
  onSave: () => void
}) {
  const canSave = Boolean(draft.title.trim()) && !disabled

  return (
    <section className="surface rounded-lg p-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-[#86868b]">Record</p>
        <button
          onClick={onSave}
          disabled={!canSave}
          className="inline-flex items-center gap-1.5 rounded-lg bg-[#1d1d1f] px-3 py-2 text-xs font-medium text-white shadow-[0_12px_26px_rgba(29,29,31,0.16)] transition-transform hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Save size={14} />
          {disabled ? 'Saving' : 'Save'}
        </button>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-1 rounded-lg bg-white/48 p-1">
        {RECORD_CATEGORIES.map(item => (
          <button
            key={item.value}
            onClick={() => onChange({ category: item.value })}
            className={`rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${draft.category === item.value ? 'bg-[#1d1d1f] text-white' : 'text-[#6e6e73] hover:text-[#1d1d1f]'}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="mt-4 space-y-3">
        <input
          value={draft.title}
          onChange={event => onChange({ title: event.target.value })}
          placeholder="Title"
          className="soft-input w-full rounded-lg px-3 py-2 text-sm text-[#1d1d1f] outline-none placeholder:text-[#86868b]"
        />
        <input
          value={draft.summary}
          onChange={event => onChange({ summary: event.target.value })}
          placeholder="One-line summary"
          className="soft-input w-full rounded-lg px-3 py-2 text-sm text-[#1d1d1f] outline-none placeholder:text-[#86868b]"
        />
        <textarea
          value={draft.body}
          onChange={event => onChange({ body: event.target.value })}
          placeholder="Notes"
          rows={6}
          className="soft-input w-full resize-none rounded-lg px-3 py-2 text-sm leading-6 text-[#1d1d1f] outline-none placeholder:text-[#86868b]"
        />
        <input
          value={draft.tags}
          onChange={event => onChange({ tags: event.target.value })}
          placeholder="tags"
          className="soft-input w-full rounded-lg px-3 py-2 text-xs text-[#1d1d1f] outline-none placeholder:text-[#86868b]"
        />
        <input
          value={draft.sources}
          onChange={event => onChange({ sources: event.target.value })}
          placeholder="sources"
          className="soft-input w-full rounded-lg px-3 py-2 text-xs text-[#1d1d1f] outline-none placeholder:text-[#86868b]"
        />
        {error && <p className="text-xs text-[#b42318]">{error}</p>}
      </div>
    </section>
  )
}

function Layer({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.06] p-4">
      <p className="text-sm font-semibold text-white">{label}</p>
      <p className="mt-1 text-xs text-white/55">{text}</p>
    </div>
  )
}
