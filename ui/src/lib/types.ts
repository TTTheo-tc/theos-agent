// --- Orchestrator States (from src/orchestrator/state_machine.py) ---
export type TaskState = 'PENDING' | 'EXECUTING' | 'REVIEWING' | 'APPROVED' | 'REJECTED' | 'EXEC_FAILED' | 'FAILED'

// --- Channel Types (from src/channels/registry.py) ---
export type ChannelType = 'telegram' | 'whatsapp' | 'discord' | 'feishu' | 'mochat' | 'dingtalk' | 'email' | 'slack' | 'qq' | 'matrix'

// --- Cost ---
export interface CostBreakdown {
  inputCost: number
  outputCost: number
  cacheCost: number
  totalCost: number
}

export interface TokenUsage {
  input: number
  output: number
  cacheRead: number
  total: number
}

// --- Session ---
export interface Session {
  id: string
  channel: ChannelType
  sessionKey: string
  startedAt: string
  lastActivity: string
  status: 'running' | 'completed' | 'failed'
  messageCount: number
  topic: string
  agents: Agent[]
  tokens: TokenUsage
  cost: CostBreakdown
}

export interface Agent {
  id: string
  sessionId: string
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  taskState: TaskState
  model: string
  provider: string
  startedAt: string
  endedAt?: string
  durationMs: number
  retryCount: number
  inputTokens: number
  outputTokens: number
  cacheHitTokens: number
  cost: CostBreakdown
  tools: ToolUse[]
  children: Agent[]
}

export interface ToolUse {
  id: string
  name: string
  summary: string
  timestamp: string
  category: string
  isError: boolean
}

// --- Channel Status ---
export interface ChannelStatus {
  channel: ChannelType
  online: boolean
  messagesTotal: number
  messagesLast24h: number
  errorsLast24h: number
  avgResponseMs: number
}

// --- Metrics ---
export interface DashboardMetrics {
  activeSessions: number
  messagesToday: number
  avgLatencyMs: number
  costToday: number
  costTrend: number
}

// --- Events (SSE) ---
export interface DashboardEvent {
  id: number
  type: 'task_created' | 'agent_started' | 'agent_tool_use' | 'agent_finished' | 'message_in' | 'message_out' | 'state_change'
  sessionId: string
  agentId?: string
  payload: Record<string, unknown>
  timestamp: string
}

// --- Cost Analytics ---
export interface CostDataPoint {
  date: string
  claude: number
  openai: number
  litellm: number
  total: number
}

export interface CostSummary {
  daily: CostDataPoint[]
  byProvider: { provider: string; cost: number; tokens: number }[]
  cacheHitRate: number
  topSessions: { sessionId: string; topic: string; cost: number }[]
}
