import { request } from './client'
import type { components } from './schema'
import type { AgentView, Run } from './types'

export type AgentCommunication = components['schemas']['AgentCommunication']
type CommunicationPage = components['schemas']['AgentCommunications']

const json = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
const agentPath = (rootId: string, agentId: string) =>
  `/runs/${encodeURIComponent(rootId)}/agents/${encodeURIComponent(agentId)}`

export const collaborationApi = {
  messages: (rootId: string, agentId: string, before: string | null, signal?: AbortSignal) => {
    const query = new URLSearchParams({ agent_id: agentId, limit: '30' })
    if (before) query.set('before', before)
    return request<CommunicationPage>(
      `/runs/${encodeURIComponent(rootId)}/agents/messages?${query}`,
      {
        signal,
      },
    )
  },
  followup: (rootId: string, agentId: string, requestId: string, input: string) =>
    request<AgentView>(
      `${agentPath(rootId, agentId)}/followup`,
      json({ request_id: requestId, input }),
    ),
  interrupt: (rootId: string, agentId: string, requestId: string) =>
    request<Run>(`${agentPath(rootId, agentId)}/interrupt`, json({ request_id: requestId })),
  close: (rootId: string, agentId: string, requestId: string) =>
    request<AgentView>(`${agentPath(rootId, agentId)}/close`, json({ request_id: requestId })),
}
