import { useCallback, useMemo, useState } from 'react'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'
import { useDelayedSync } from '@/lib/use-delayed-sync'

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

type InstinctRule = {
  id: string | null
  text: string
  meta: Record<string, string>
  section?: string
}

type InstinctDomain = {
  id: string
  category: string
  domain: string
  keywords: string[]
  skills: string[]
  tools: string[]
  context: string
  path: string
}

type RecallTarget = {
  target_id: string
  score: number
  components: Record<string, number>
  recall_count: number
  distinct_queries: number
  distinct_days: number
  last_recalled_at: string
  max_score: number
}

type ReflectionEvent = {
  file: string
  timestamp: string
  session_key: string
  status: string
  demand_class: string
  summary: string
  domains: string[]
  rule_count: number
}

type InstinctPayload = {
  framework: {
    core: { rules: Array<{ title: string; text: string }> }
    domains: InstinctDomain[]
    scripts: Array<{ name: string; path: string; exists: boolean }>
  }
  runtime: {
    path: string
    exists: boolean
    status: Record<string, string | number>
    rules: {
      active: { count: number; rules: InstinctRule[] }
      probation: { count: number; rules: InstinctRule[] }
      candidates: { count: number; rules: InstinctRule[] }
    }
    live_rules: Array<Record<string, unknown>>
    recall: {
      targets: RecallTarget[]
      journal_tail: Array<Record<string, unknown>>
    }
    events: {
      recent: ReflectionEvent[]
      memory_tail: Array<Record<string, unknown>>
    }
    lessons: Array<{ file: string; title: string; snippet: string }>
  }
}

const NODE_TYPES = ['rule', 'task', 'research', 'lesson']

function formatDate(value: string): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleDateString()
}

function formatDateTime(value: unknown): string {
  if (typeof value !== 'string' || !value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function asCount(value: unknown): string {
  return typeof value === 'number' ? String(value) : '0'
}

function scoreTone(score: number): string {
  if (score >= 0.75) return 'text-[#0071e3]'
  if (score >= 0.5) return 'text-[#b36b00]'
  return 'text-[#86868b]'
}

function statusLabel(status: Record<string, string | number>): string {
  const last = status.last_evolved
  return typeof last === 'string' && last !== 'never' ? `Last evolved ${last}` : 'Waiting for first evolve'
}

export default function MemoryPage() {
  const [view, setView] = useState<'instinct' | 'graph'>('instinct')
  const [nodeType, setNodeType] = useState('rule')
  const [nodes, setNodes] = useState<KGNode[]>([])
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<KGNode[]>([])
  const [selected, setSelected] = useState<KGNode | null>(null)
  const [loading, setLoading] = useState(false)
  const [instinct, setInstinct] = useState<InstinctPayload | null>(null)
  const [instinctLoading, setInstinctLoading] = useState(false)
  const [instinctError, setInstinctError] = useState('')

  const loadNodes = useCallback(() => {
    setLoading(true)
    return fetch(`/api/memory/nodes?type=${nodeType}&limit=50`)
      .then(r => r.json())
      .then(setNodes)
      .catch(() => setNodes([]))
      .finally(() => setLoading(false))
  }, [nodeType])

  useDelayedSync(loadNodes, 15000)

  const loadInstinct = useCallback(() => {
    setInstinctLoading(true)
    setInstinctError('')
    return fetch('/api/memory/instinct')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(setInstinct)
      .catch(() => {
        setInstinct(null)
        setInstinctError('Instinct data is offline.')
      })
      .finally(() => setInstinctLoading(false))
  }, [])

  useDelayedSync(loadInstinct, 10000)

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

  const domainsByCategory = useMemo(() => {
    const grouped = new Map<string, InstinctDomain[]>()
    for (const domain of instinct?.framework.domains ?? []) {
      grouped.set(domain.category, [...(grouped.get(domain.category) ?? []), domain])
    }
    return Array.from(grouped.entries())
  }, [instinct])

  const status = instinct?.runtime.status ?? {}
  const activeRules = instinct?.runtime.rules.active.rules ?? []
  const candidateRules = instinct?.runtime.rules.candidates.rules ?? []
  const recallTargets = instinct?.runtime.recall.targets ?? []
  const recentEvents = instinct?.runtime.events.recent ?? []
  const coreRules = instinct?.framework.core.rules ?? []
  const domainCount = instinct?.framework.domains.length ?? 0

  return (
    <>
      <Header onRefresh={loadInstinct} />
      <main className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-8 px-10 py-10">
          <div className="flex items-start justify-between gap-6">
            <div className="max-w-3xl">
              <p className="text-sm font-medium text-[#86868b]">Memory</p>
              <h2 className="mt-2 text-[44px] font-semibold leading-[1.05] text-[#1d1d1f]">Instinct, made visible.</h2>
              <p className="mt-4 max-w-2xl text-base leading-7 text-[#6e6e73]">
                A quiet surface for the rules, recall signals, and framework knowledge that shape the agent.
              </p>
            </div>
            <div className="surface-soft flex rounded-lg p-1">
              <button
                onClick={() => setView('instinct')}
                className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${view === 'instinct' ? 'bg-[#1d1d1f] text-white' : 'text-[#6e6e73] hover:text-[#1d1d1f]'}`}
              >
                Instinct
              </button>
              <button
                onClick={() => setView('graph')}
                className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${view === 'graph' ? 'bg-[#1d1d1f] text-white' : 'text-[#6e6e73] hover:text-[#1d1d1f]'}`}
              >
                Graph
              </button>
            </div>
          </div>

          {view === 'instinct' ? (
            <div className="space-y-8">
              <section className="grid grid-cols-12 gap-5">
                <div className="col-span-7 rounded-lg bg-[linear-gradient(135deg,#1d1d1f_0%,#2b2b31_100%)] p-8 text-white shadow-[0_28px_75px_rgba(29,29,31,0.22)]">
                  <div className="flex items-start justify-between gap-6">
                    <div>
                      <p className="text-sm text-white/55">Runtime</p>
                      <h3 className="mt-3 text-3xl font-semibold">Instinct framework</h3>
                      <p className="mt-3 max-w-xl text-sm leading-6 text-white/62">
                        {instinctError || statusLabel(status)}
                      </p>
                    </div>
                    <span className="rounded-full border border-white/10 px-3 py-1 text-xs text-white/60">
                      {instinctLoading ? 'Syncing' : instinct?.runtime.exists ? 'Live' : 'Offline'}
                    </span>
                  </div>
                  <div className="mt-10 grid grid-cols-4 gap-6">
                    <Metric label="Active" value={asCount(status.active_rules)} inverted />
                    <Metric label="Recall" value={asCount(status.recall_targets)} inverted />
                    <Metric label="Domains" value={String(domainCount)} inverted />
                    <Metric label="Events" value={asCount(status.events)} inverted />
                  </div>
                </div>

                <div className="surface col-span-5 rounded-lg p-8">
                  <p className="text-sm font-medium text-[#86868b]">Brainstem</p>
                  <div className="mt-5 space-y-4">
                    {coreRules.slice(0, 3).map(rule => (
                      <div key={rule.title}>
                        <p className="text-sm font-semibold text-[#1d1d1f]">{rule.title}</p>
                        <p className="mt-1 line-clamp-2 text-sm leading-6 text-[#6e6e73]">{rule.text}</p>
                      </div>
                    ))}
                    {!instinctLoading && coreRules.length === 0 && (
                      <p className="text-sm leading-6 text-[#86868b]">No core rules found yet.</p>
                    )}
                  </div>
                </div>
              </section>

              <section className="grid grid-cols-12 gap-5">
                <div className="col-span-7">
                  <RulesSurface activeRules={activeRules} candidateRules={candidateRules} />
                </div>
                <div className="col-span-5 space-y-5">
                  <RecallSurface targets={recallTargets} />
                  <EventsSurface events={recentEvents} />
                </div>
              </section>

              <section className="surface rounded-lg p-8">
                <div className="flex items-end justify-between gap-5">
                  <div>
                    <p className="text-sm font-medium text-[#86868b]">Framework</p>
                    <h3 className="mt-2 text-2xl font-semibold text-[#1d1d1f]">Domains</h3>
                  </div>
                  <Badge variant="outline">{domainCount} domains</Badge>
                </div>
                <div className="mt-6 grid grid-cols-3 gap-4">
                  {domainsByCategory.slice(0, 6).map(([category, domains]) => (
                    <div key={category} className="surface-soft rounded-lg p-5 transition-transform duration-200 hover:-translate-y-0.5">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-semibold text-[#1d1d1f]">{category}</p>
                        <span className="text-xs text-[#86868b]">{domains.length}</span>
                      </div>
                      <div className="mt-4 space-y-3">
                        {domains.slice(0, 3).map(domain => (
                          <div key={domain.id}>
                            <p className="text-sm font-medium text-[#1d1d1f]">{domain.id}</p>
                            <p className="mt-1 line-clamp-1 text-xs text-[#86868b]">
                              {domain.context || domain.keywords.slice(0, 5).join(', ')}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                {domainCount === 0 && <p className="mt-6 text-sm text-[#86868b]">No domain catalog found yet.</p>}
              </section>
            </div>
          ) : (
            <GraphMemory
              nodeType={nodeType}
              setNodeType={setNodeType}
              nodes={nodes}
              selected={selected}
              setSelected={setSelected}
              loading={loading}
              query={query}
              setQuery={setQuery}
              searchResults={searchResults}
              doSearch={doSearch}
            />
          )}
        </div>
      </main>
    </>
  )
}

function Metric({ label, value, inverted = false }: { label: string; value: string; inverted?: boolean }) {
  return (
    <div>
      <p className={`text-xs font-medium ${inverted ? 'text-white/45' : 'text-[#86868b]'}`}>{label}</p>
      <p className={`mt-2 text-3xl font-semibold ${inverted ? 'text-white' : 'text-[#1d1d1f]'}`}>{value}</p>
    </div>
  )
}

function RulesSurface({ activeRules, candidateRules }: { activeRules: InstinctRule[]; candidateRules: InstinctRule[] }) {
  const topRules = activeRules.slice(0, 6)
  const nextRules = candidateRules.slice(0, 4)

  return (
    <section className="surface rounded-lg p-8">
      <div className="flex items-end justify-between gap-5">
        <div>
          <p className="text-sm font-medium text-[#86868b]">Rules</p>
          <h3 className="mt-2 text-2xl font-semibold text-[#1d1d1f]">Current behavior</h3>
        </div>
        <Badge variant="outline">{activeRules.length} active</Badge>
      </div>

      <div className="mt-7 divide-y divide-black/[0.04]">
        {topRules.map((rule, index) => (
          <RuleRow key={`${rule.id ?? index}-${rule.text}`} rule={rule} />
        ))}
        {topRules.length === 0 && <p className="py-8 text-sm text-[#86868b]">No active rules yet.</p>}
      </div>

      {nextRules.length > 0 && (
        <div className="surface-soft mt-8 rounded-lg p-5">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-[#1d1d1f]">Candidates</p>
            <span className="text-xs text-[#86868b]">{candidateRules.length}</span>
          </div>
          <div className="mt-4 space-y-3">
            {nextRules.map((rule, index) => (
              <p key={`${rule.id ?? index}-${rule.text}`} className="line-clamp-2 text-sm leading-6 text-[#6e6e73]">
                {rule.text}
              </p>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function RuleRow({ rule }: { rule: InstinctRule }) {
  return (
    <div className="py-5">
      <div className="flex items-start justify-between gap-5">
        <p className="text-sm leading-6 text-[#1d1d1f]">{rule.text}</p>
        {rule.id && <span className="shrink-0 text-xs font-medium text-[#86868b]">{rule.id}</span>}
      </div>
      {(rule.meta.domains || rule.meta.class || rule.meta.conf) && (
        <p className="mt-2 text-xs text-[#86868b]">
          {[rule.meta.domains, rule.meta.class, rule.meta.conf ? `conf ${rule.meta.conf}` : ''].filter(Boolean).join(' · ')}
        </p>
      )}
    </div>
  )
}

function RecallSurface({ targets }: { targets: RecallTarget[] }) {
  return (
    <section className="surface rounded-lg p-6">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[#1d1d1f]">Recall</h3>
        <Badge variant="outline">{targets.length}</Badge>
      </div>
      <div className="mt-5 space-y-4">
        {targets.slice(0, 5).map(target => (
          <div key={target.target_id}>
            <div className="flex items-center justify-between gap-4">
              <p className="truncate font-mono text-xs text-[#1d1d1f]">{target.target_id}</p>
              <span className={`text-sm font-semibold ${scoreTone(target.score)}`}>{Math.round(target.score * 100)}%</span>
            </div>
            <p className="mt-1 text-xs text-[#86868b]">
              {target.recall_count} recalls · {target.distinct_queries} queries · {formatDateTime(target.last_recalled_at)}
            </p>
          </div>
        ))}
        {targets.length === 0 && <p className="text-sm text-[#86868b]">No recall targets yet.</p>}
      </div>
    </section>
  )
}

function EventsSurface({ events }: { events: ReflectionEvent[] }) {
  return (
    <section className="surface rounded-lg p-6">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[#1d1d1f]">Recent events</h3>
        <Badge variant="outline">{events.length}</Badge>
      </div>
      <div className="mt-5 space-y-4">
        {events.slice(0, 4).map(event => (
          <div key={event.file}>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm font-medium text-[#1d1d1f]">{event.status || 'unknown'}</p>
              <span className="text-xs text-[#86868b]">{formatDateTime(event.timestamp)}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-sm leading-6 text-[#6e6e73]">{event.summary || event.file}</p>
          </div>
        ))}
        {events.length === 0 && <p className="text-sm text-[#86868b]">No reflection events yet.</p>}
      </div>
    </section>
  )
}

function GraphMemory({
  nodeType,
  setNodeType,
  nodes,
  selected,
  setSelected,
  loading,
  query,
  setQuery,
  searchResults,
  doSearch,
}: {
  nodeType: string
  setNodeType: (value: string) => void
  nodes: KGNode[]
  selected: KGNode | null
  setSelected: (node: KGNode | null) => void
  loading: boolean
  query: string
  setQuery: (value: string) => void
  searchResults: KGNode[]
  doSearch: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-end gap-2">
        {NODE_TYPES.map(t => (
          <button
            key={t}
            onClick={() => {
              setNodeType(t)
              setSelected(null)
            }}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${nodeType === t ? 'bg-[#1d1d1f] text-white' : 'bg-white/65 text-[#6e6e73] hover:bg-white hover:text-[#1d1d1f]'}`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-12 gap-5">
        <section className="surface col-span-5 min-h-[620px] rounded-lg p-6">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold text-[#1d1d1f]">Nodes</h3>
            <Badge variant="outline">{loading ? 'loading' : `${nodes.length}`}</Badge>
          </div>
          <div className="mt-5 max-h-[540px] overflow-y-auto space-y-2 pr-1">
            {nodes.map(node => (
              <button
                key={node.id}
                onClick={() => setSelected(node)}
                className={`w-full rounded-lg px-4 py-3 text-left transition-all duration-200 ${selected?.id === node.id ? 'bg-[#1d1d1f] text-white shadow-[0_14px_34px_rgba(29,29,31,0.18)]' : 'bg-white/48 text-[#1d1d1f] hover:-translate-y-0.5 hover:bg-white/78 hover:shadow-[0_12px_30px_rgba(29,29,31,0.055)]'}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{node.title || node.id}</p>
                    <p className={`mt-1 line-clamp-2 text-xs ${selected?.id === node.id ? 'text-white/60' : 'text-[#86868b]'}`}>{node.content}</p>
                  </div>
                  <span className={`shrink-0 text-xs ${selected?.id === node.id ? 'text-white/60' : 'text-[#86868b]'}`}>
                    {Math.round((node.importance ?? 0) * 100)}%
                  </span>
                </div>
              </button>
            ))}
            {!loading && nodes.length === 0 && <p className="text-sm text-[#86868b]">No {nodeType} nodes found.</p>}
          </div>
        </section>

        <section className="surface col-span-4 min-h-[620px] rounded-lg p-6">
          <h3 className="text-base font-semibold text-[#1d1d1f]">Detail</h3>
          {selected ? (
            <div className="mt-5">
              <div className="flex items-center gap-2">
                <Badge>{selected.node_type}</Badge>
                <h4 className="text-sm font-semibold text-[#1d1d1f]">{selected.title || selected.id}</h4>
              </div>
              <div className="mt-3 flex gap-4 text-xs text-[#86868b]">
                <span>Created {formatDate(selected.created_at)}</span>
                <span>Updated {formatDate(selected.updated_at)}</span>
              </div>
              <p className="mt-6 max-h-[460px] overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-[#515154]">{selected.content}</p>
            </div>
          ) : (
            <p className="mt-5 text-sm text-[#86868b]">Select a node.</p>
          )}
        </section>

        <section className="surface col-span-3 min-h-[620px] rounded-lg p-6">
          <h3 className="text-base font-semibold text-[#1d1d1f]">Search</h3>
          <div className="mt-5 flex gap-2">
            <input
              placeholder="Search memory..."
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && doSearch()}
              className="soft-input min-w-0 flex-1 rounded-lg px-3 py-2 text-sm text-[#1d1d1f] outline-none transition-colors placeholder:text-[#86868b] focus:border-[#0071e3]"
            />
            <button onClick={doSearch} className="rounded-lg bg-[#1d1d1f] px-3 py-2 text-sm font-medium text-white">Go</button>
          </div>
          <div className="mt-5 max-h-[500px] overflow-y-auto space-y-2 pr-1">
            {searchResults.map(result => (
              <button
                key={result.id}
                onClick={() => setSelected(result)}
                className="w-full rounded-lg bg-white/48 px-3 py-2 text-left transition-all duration-200 hover:-translate-y-0.5 hover:bg-white/78 hover:shadow-[0_12px_30px_rgba(29,29,31,0.055)]"
              >
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-[#86868b]">{result.node_type}</span>
                  <p className="truncate text-xs font-medium text-[#1d1d1f]">{result.title}</p>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] text-[#86868b]">{result.content}</p>
              </button>
            ))}
            {query && searchResults.length === 0 && <p className="text-sm text-[#86868b]">No search results.</p>}
          </div>
        </section>
      </div>
    </div>
  )
}
