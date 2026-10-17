import { request } from './client'
import type { Workspace, WorkspaceLifecycle, WorkspaceRename } from './types'
const root = (id: string) => `/workspaces/${encodeURIComponent(id)}`
const post = (value: unknown) => ({ method: 'POST', body: JSON.stringify(value) })
export const workspacesApi = {
  all: () => request<Workspace[]>('/workspaces?include_archived=true'),
  rename: (id: string, input: WorkspaceRename) =>
    request<Workspace>(`${root(id)}/rename`, post(input)),
  lifecycle: (id: string, action: 'archive' | 'restore' | 'remove', input: WorkspaceLifecycle) =>
    request<Workspace>(`${root(id)}/${action}`, post(input)),
}
