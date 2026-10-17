import { request } from './client'
import type {
  DefaultsSave,
  DefaultsView,
  WorkspaceDefaultsView,
  WorkspaceDefaultsSave,
  SandboxCapability,
} from './types'
export const settingsApi = {
  sandbox: () => request<SandboxCapability[]>('/settings/sandbox'),
  runtime: () => request<DefaultsView>('/settings/runtime'),
  saveRuntime: (input: DefaultsSave) =>
    request<DefaultsView>('/settings/runtime', { method: 'POST', body: JSON.stringify(input) }),
  workspaceRuntime: (id: string) =>
    request<WorkspaceDefaultsView>(`/workspaces/${encodeURIComponent(id)}/settings/runtime`),
  saveWorkspaceRuntime: (id: string, input: WorkspaceDefaultsSave) =>
    request<WorkspaceDefaultsView>(`/workspaces/${encodeURIComponent(id)}/settings/runtime`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
}
