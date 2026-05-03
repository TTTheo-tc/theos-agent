
import { useEffect, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  useNodesState,
  useEdgesState,
  Position,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { Agent, TaskState } from '@/lib/types'

const STATE_BG: Record<TaskState, string> = {
  PENDING: '#334155',
  EXECUTING: '#1e40af',
  REVIEWING: '#92400e',
  APPROVED: '#166534',
  REJECTED: '#9a3412',
  EXEC_FAILED: '#991b1b',
  FAILED: '#991b1b',
}

/** Flatten a tree of agents into a flat list, preserving parent-child relationships. */
function flattenAgents(agents: Agent[], parentId?: string): { agent: Agent; parentId?: string }[] {
  const result: { agent: Agent; parentId?: string }[] = []
  for (const agent of agents) {
    result.push({ agent, parentId })
    if (agent.children?.length) {
      result.push(...flattenAgents(agent.children, agent.id))
    }
  }
  return result
}

function agentsToFlow(agents: Agent[]): { nodes: Node[]; edges: Edge[] } {
  const flat = flattenAgents(agents)

  const nodes: Node[] = flat.map(({ agent }, i) => ({
    id: agent.id,
    position: { x: (i % 4) * 220, y: Math.floor(i / 4) * 120 },
    data: {
      label: (
        <div className="text-left">
          <div className="text-[10px] font-semibold text-slate-100 truncate max-w-[160px]">
            {agent.name || agent.id.slice(0, 12)}
          </div>
          <div className="text-[9px] text-slate-400 mt-0.5">
            {agent.taskState} | ${(agent.cost?.totalCost ?? 0).toFixed(4)}
          </div>
        </div>
      ),
    },
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    style: {
      background: STATE_BG[agent.taskState] ?? '#334155',
      border: '1px solid rgba(148,163,184,0.2)',
      borderRadius: '8px',
      padding: '8px 12px',
      width: 180,
    },
  }))

  const edges: Edge[] = flat
    .filter(({ parentId }) => parentId !== undefined)
    .map(({ agent, parentId }) => ({
      id: `${parentId}-${agent.id}`,
      source: parentId!,
      target: agent.id,
      animated: agent.status === 'running',
      style: { stroke: '#475569' },
    }))

  return { nodes, edges }
}

export function DAGView({ agents }: { agents: Agent[] }) {
  const { nodes: initNodes, edges: initEdges } = useMemo(() => agentsToFlow(agents), [agents])
  const [nodes, setNodes, onNodesChange] = useNodesState(initNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initEdges)

  useEffect(() => {
    const { nodes: n, edges: e } = agentsToFlow(agents)
    setNodes(n)
    setEdges(e)
  }, [agents, setNodes, setEdges])

  if (agents.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-600 rounded-lg border border-slate-800 bg-slate-900/30">
        Select a session to view agent DAG
      </div>
    )
  }

  return (
    <div className="h-full w-full rounded-lg border border-slate-800 overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        proOptions={{ hideAttribution: true }}
        className="bg-[#020617]"
      >
        <Background color="#1e293b" gap={20} />
        <Controls className="!bg-slate-900 !border-slate-700 !text-slate-400" />
      </ReactFlow>
    </div>
  )
}
