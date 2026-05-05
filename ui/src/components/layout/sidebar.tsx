import { Link, useLocation } from 'react-router'
import { Bot, Brain, CalendarClock, BookOpenText, ListChecks } from 'lucide-react'
import { cn } from '@/lib/utils'

const SECTIONS = [
  {
    label: 'Workspace',
    items: [
      { href: '/memory', label: 'Memory', icon: Brain },
      { href: '/wiki', label: 'Wiki', icon: BookOpenText },
      { href: '/cron', label: 'Cron', icon: CalendarClock },
      { href: '/plans', label: 'Plans', icon: ListChecks },
    ],
  },
]

export function Sidebar() {
  const { pathname } = useLocation()

  return (
    <aside className="w-60 border-r border-white/60 bg-white/60 backdrop-blur-xl flex flex-col shrink-0 shadow-[inset_-1px_0_rgba(29,29,31,0.045)]">
      <div className="h-16 px-5 flex items-center gap-3 shadow-[inset_0_-1px_rgba(29,29,31,0.045)]">
        <div className="w-8 h-8 bg-[#1d1d1f] rounded-lg flex items-center justify-center shadow-sm">
          <Bot size={17} className="text-white" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-[#1d1d1f] leading-tight">TheOS</h1>
          <p className="text-[11px] text-[#86868b] leading-tight">Personal OS</p>
        </div>
      </div>
      <nav className="flex-1 p-4 space-y-5 overflow-y-auto">
        {SECTIONS.map(({ label, items }) => (
          <div key={label}>
            <p className="text-[11px] font-medium text-[#86868b] px-3 mb-2">{label}</p>
            <div className="space-y-1">
              {items.map(({ href, label: itemLabel, icon: Icon }) => (
                <Link
                  key={href}
                  to={href}
                  className={cn(
                    'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                    pathname === href
                      ? 'bg-[#1d1d1f] text-white shadow-sm'
                      : 'text-[#6e6e73] hover:text-[#1d1d1f] hover:bg-black/[0.04]'
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
