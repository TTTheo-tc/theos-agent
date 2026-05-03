import { Header } from '@/components/layout/header'
import { ChannelStatusCards } from '@/components/channels/channel-status'

export default function ChannelsPage() {
  return (
    <>
      <Header />
      <main className="flex-1 p-6">
        <ChannelStatusCards />
      </main>
    </>
  )
}
