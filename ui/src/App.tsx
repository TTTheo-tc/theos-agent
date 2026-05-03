import { BrowserRouter, Routes, Route } from 'react-router'
import { Sidebar } from '@/components/layout/sidebar'
import DashboardPage from '@/pages/Dashboard'
import TimelinePage from '@/pages/Timeline'
import CostPage from '@/pages/Cost'
import ChannelsPage from '@/pages/Channels'
import MemoryPage from '@/pages/Memory'
import CronPage from '@/pages/Cron'
import LogsPage from '@/pages/Logs'
import ConfigPage from '@/pages/Config'
import ToolsPage from '@/pages/Tools'
import SettingsPage from '@/pages/Settings'

export function App() {
  return (
    <BrowserRouter>
      <div className="h-screen flex overflow-hidden">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/timeline" element={<TimelinePage />} />
            <Route path="/cost" element={<CostPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/cron" element={<CronPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/config" element={<ConfigPage />} />
            <Route path="/tools" element={<ToolsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}
