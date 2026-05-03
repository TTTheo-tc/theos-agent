import { Header } from '@/components/layout/header'
import { CostCharts } from '@/components/viz/cost-charts'

export default function CostPage() {
  return (
    <>
      <Header />
      <main className="flex-1 p-6">
        <CostCharts />
      </main>
    </>
  )
}
