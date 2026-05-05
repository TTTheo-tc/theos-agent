import { BrowserRouter, Navigate, Routes, Route } from 'react-router'
import { Sidebar } from '@/components/layout/sidebar'
import MemoryPage from '@/pages/Memory'
import WikiPage from '@/pages/Wiki'
import CronPage from '@/pages/Cron'
import PlansPage from '@/pages/Plans'

export function App() {
  return (
    <BrowserRouter>
      <div className="h-screen flex overflow-hidden bg-[linear-gradient(180deg,#fbfbfd_0%,#f5f5f7_48%,#efeff2_100%)] text-[#1d1d1f]">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <Routes>
            <Route path="/" element={<Navigate to="/memory" replace />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/wiki" element={<WikiPage />} />
            <Route path="/cron" element={<CronPage />} />
            <Route path="/plans" element={<PlansPage />} />
            <Route path="*" element={<Navigate to="/memory" replace />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}
