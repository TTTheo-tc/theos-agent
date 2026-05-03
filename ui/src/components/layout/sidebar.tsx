import { Link, useLocation } from 'react-router'
import {
  LayoutDashboard, Clock, DollarSign, Radio, Bot,
  ScrollText, Brain, CalendarClock, FileJson, Wrench, Settings,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const SECTIONS = [
  {
    label: 'Observe',
    items: [
      { href: '/', label: 'Overview', icon: LayoutDashboard },
      { href: '/timeline', label: 'Timeline', icon: Clock },
      { href: '/cost', label: 'Cost', icon: DollarSign },
      { href: '/channels', label: 'Channels', icon: Radio },
      { href: '/logs', label: 'Logs', icon: ScrollText },
    ],
  },
  {
    label: 'Manage',
    items: [
      { href: '/memory', label: 'Memory', icon: Brain },
      { href: '/cron', label: 'Cron', icon: CalendarClock },
      { href: '/config', label: 'Config', icon: FileJson },
      { href: '/tools', label: 'Tools', icon: Wrench },
      { href: '/settings', label: 'Settings', icon: Settings },
    ],
  },
]

export function Sidebar() {
  const { pathname } = useLocation()

  return (
    <aside className="w-56 border-r border-slate-800 bg-[#0F172A] flex flex-col shrink-0">
      <div className="h-14 px-4 flex items-center gap-3 border-b border-slate-800">
        <div className="w-8 h-8 bg-green-600 rounded-lg flex items-center justify-center">
          <Bot size={18} className="text-white" />
        </div>
        <div>
          <h1 className="text-sm font-bold text-slate-100 leading-tight">Superbot</h1>
          <p className="text-[10px] text-slate-500 leading-tight">Agent Dashboard</p>
        </div>
      </div>
      <nav className="flex-1 p-3 space-y-4 overflow-y-auto">
        {SECTIONS.map(({ label, items }) => (
          <div key={label}>
            <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider px-3 mb-1">{label}</p>
            <div className="space-y-0.5">
              {items.map(({ href, label: itemLabel, icon: Icon }) => (
                <Link
                  key={href}
                  to={href}
                  className={cn(
                    'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                    pathname === href
                      ? 'bg-slate-800 text-green-400'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                  )}
                >
                  <Icon size={16} />
                  {itemLabel}
                </Link>
              ))}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  )
}
