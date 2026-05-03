import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Search } from 'lucide-react'
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
} from '@/components/ui/command'

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    document.addEventListener('keydown', down)
    return () => document.removeEventListener('keydown', down)
  }, [])

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/60 hover:bg-slate-800 text-slate-500 text-xs transition-colors"
      >
        <Search size={12} />
        Search...
        <kbd className="ml-2 text-[10px] text-slate-600 bg-slate-900 px-1.5 py-0.5 rounded">⌘K</kbd>
      </button>
      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder="Search sessions, agents, channels..." />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          <CommandGroup heading="Pages">
            <CommandItem onSelect={() => { navigate('/'); setOpen(false) }}>Overview</CommandItem>
            <CommandItem onSelect={() => { navigate('/timeline'); setOpen(false) }}>Timeline</CommandItem>
            <CommandItem onSelect={() => { navigate('/cost'); setOpen(false) }}>Cost Analytics</CommandItem>
            <CommandItem onSelect={() => { navigate('/channels'); setOpen(false) }}>Channels</CommandItem>
            <CommandItem onSelect={() => { navigate('/logs'); setOpen(false) }}>Logs</CommandItem>
            <CommandItem onSelect={() => { navigate('/memory'); setOpen(false) }}>Memory</CommandItem>
            <CommandItem onSelect={() => { navigate('/cron'); setOpen(false) }}>Cron</CommandItem>
            <CommandItem onSelect={() => { navigate('/config'); setOpen(false) }}>Config</CommandItem>
            <CommandItem onSelect={() => { navigate('/tools'); setOpen(false) }}>Tools</CommandItem>
            <CommandItem onSelect={() => { navigate('/settings'); setOpen(false) }}>Settings</CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </>
  )
}
