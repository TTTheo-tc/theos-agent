
import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

type ChannelType = 'telegram' | 'whatsapp' | 'discord' | 'feishu' | 'mochat' | 'dingtalk' | 'email' | 'slack' | 'qq' | 'matrix'

interface ChannelRow {
  channel: ChannelType
  online: number
  messages_total: number
  messages_last_24h: number
  errors_last_24h: number
  avg_response_ms: number
}

const CHANNEL_LABELS: Record<ChannelType, string> = {
  telegram: 'Telegram', discord: 'Discord', whatsapp: 'WhatsApp', feishu: 'Feishu',
  slack: 'Slack', qq: 'QQ', dingtalk: 'DingTalk', email: 'Email', matrix: 'Matrix', mochat: 'MoChat',
}

const CHANNEL_BORDER: Record<ChannelType, string> = {
  telegram: 'border-blue-500/30', discord: 'border-indigo-500/30', whatsapp: 'border-green-500/30',
  feishu: 'border-blue-400/30', slack: 'border-purple-500/30', qq: 'border-cyan-500/30',
  dingtalk: 'border-blue-600/30', email: 'border-amber-500/30', matrix: 'border-teal-500/30', mochat: 'border-pink-500/30',
}

export function ChannelStatusCards() {
  const [channels, setChannels] = useState<ChannelRow[]>([])

  useEffect(() => {
    fetch('/api/channels').then(r => r.json()).then(setChannels).catch(() => {})
    const interval = setInterval(() => {
      fetch('/api/channels').then(r => r.json()).then(setChannels).catch(() => {})
    }, 10000)
    return () => clearInterval(interval)
  }, [])

  if (channels.length === 0) {
    return <p className="text-sm text-slate-600">No channel data available yet</p>
  }

  return (
    <div className="grid grid-cols-5 gap-4">
      {channels.map((ch) => (
        <Card key={ch.channel} className={cn('bg-slate-900/50 border-slate-800', CHANNEL_BORDER[ch.channel] ?? '')}>
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold text-slate-200">
                {CHANNEL_LABELS[ch.channel] ?? ch.channel}
              </span>
              <div className={cn('w-2.5 h-2.5 rounded-full', ch.online ? 'bg-green-500' : 'bg-slate-600')} />
            </div>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-500">Messages (24h)</span>
                <span className="text-slate-300 font-mono">{(ch.messages_last_24h ?? 0).toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Errors (24h)</span>
                <span className={cn('font-mono', (ch.errors_last_24h ?? 0) > 0 ? 'text-red-400' : 'text-slate-300')}>
                  {ch.errors_last_24h ?? 0}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Avg Latency</span>
                <span className="text-slate-300 font-mono">{Math.round(ch.avg_response_ms ?? 0)}ms</span>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
