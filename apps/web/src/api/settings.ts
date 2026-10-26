import { request } from './client'
import type {
  DefaultsSave,
  DefaultsView,
  WorkspaceDefaultsView,
  WorkspaceDefaultsSave,
  SandboxCapability,
} from './types'

export type ExperienceValues = {
  theme: 'system' | 'light' | 'dark'
  input_mode: 'queue' | 'steer'
}
export type ExperienceView = ExperienceValues & { version: number; initialized: boolean }
export type ExperienceSave = ExperienceValues & { expected_version: number; request_id: string }

export const settingsApi = {
  experience: (signal?: AbortSignal) => request<ExperienceView>('/settings/experience', { signal }),
  saveExperience: (input: ExperienceSave) =>
    request<ExperienceView>('/settings/experience', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
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
