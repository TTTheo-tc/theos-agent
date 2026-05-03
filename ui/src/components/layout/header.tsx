
import { RefreshCw } from 'lucide-react'
import { CommandPalette } from './command-palette'

export function Header({ onRefresh }: { onRefresh?: () => void }) {
  return (
    <header className="h-14 border-b border-slate-800 bg-[#0F172A]/80 backdrop-blur-sm px-6 flex items-center justify-between shrink-0">
      <div />
      <div className="flex items-center gap-2">
        <CommandPalette />
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="p-2 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={16} />
          </button>
        )}
      </div>
    </header>
  )
}
