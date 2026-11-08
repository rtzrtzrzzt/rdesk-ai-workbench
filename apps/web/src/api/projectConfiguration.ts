import { request } from './client'
import type { components } from './schema'

export type ProjectConfigurationView = components['schemas']['ProjectConfigurationView']
export type DataDirectoryView = components['schemas']['DataDirectoryView']

export const projectConfigurationApi = {
  directory: () => request<DataDirectoryView>('/settings/data-directory'),
  project: (workspaceId: string) =>
    request<ProjectConfigurationView>(
      `/workspaces/${encodeURIComponent(workspaceId)}/project-configuration`,
    ),
}
