import { request } from './client'
import type {
  MCPDiscover,
  MCPList,
  MCPSave,
  MCPView,
  MCPSnapshotView,
  RequestResult,
} from './types'
const root = (workspaceId: string) =>
  `/workspaces/${encodeURIComponent(workspaceId)}/mcp/connections`
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const mcpApi = {
  list: (workspaceId: string) => request<MCPList>(root(workspaceId)),
  save: (workspaceId: string, id: string, input: MCPSave) =>
    request<MCPView>(`${root(workspaceId)}/${encodeURIComponent(id)}`, post(input)),
  discover: (workspaceId: string, id: string, input: MCPDiscover) =>
    request<MCPSnapshotView>(
      `${root(workspaceId)}/${encodeURIComponent(id)}/discover`,
      post(input),
    ),
  receipt: (requestId: string) =>
    request<RequestResult>(`/requests/${encodeURIComponent(requestId)}`),
}
