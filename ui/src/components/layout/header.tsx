import { RefreshCw } from 'lucide-react'
import { CommandPalette } from './command-palette'

export function Header({ onRefresh }: { onRefresh?: () => void }) {
  return (
    <header className="h-16 bg-[#f5f5f7]/70 backdrop-blur-xl px-8 flex items-center justify-between shrink-0 shadow-[inset_0_-1px_rgba(29,29,31,0.045)]">
      <div />
      <div className="flex items-center gap-2">
        <CommandPalette />
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="p-2 rounded-lg text-[#86868b] hover:text-[#1d1d1f] hover:bg-black/[0.04] transition-colors"
            title="Refresh"
          >
            <RefreshCw size={16} />
          </button>
        )}
      </div>
    </header>
  )
}
