import { request } from './client'
import type { components } from './schema'

export type ExtensionSource = components['schemas']['ExtensionSource']
export type ExtensionInstall = components['schemas']['ExtensionInstall'] & {
  expected_content_digest?: string | null
}
export type ExtensionUpgrade = components['schemas']['ExtensionUpgrade'] & {
  expected_content_digest?: string | null
}
export type ExtensionChange = components['schemas']['ExtensionChange']
export type ExtensionRemove = components['schemas']['ExtensionRemove']
export type ExtensionMCPImport = components['schemas']['ExtensionMCPImport']
export type ExtensionView = components['schemas']['ExtensionView']
export type ExtensionList = components['schemas']['ExtensionList']
export type ExtensionInspection = components['schemas']['ExtensionInspection'] & {
  content_digest: string
}

const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/extensions`
const item = (workspaceId: string, id: string) => `${root(workspaceId)}/${encodeURIComponent(id)}`
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })

export const extensionsApi = {
  list: (workspaceId: string, signal?: AbortSignal) =>
    request<ExtensionList>(root(workspaceId), { signal }),
  inspect: (workspaceId: string, input: ExtensionSource) =>
    request<ExtensionInspection>(`${root(workspaceId)}/inspect`, post(input)),
  install: (workspaceId: string, input: ExtensionInstall) =>
    request<ExtensionView>(`${root(workspaceId)}/install`, post(input)),
  upgrade: (workspaceId: string, id: string, input: ExtensionUpgrade) =>
    request<ExtensionView>(`${item(workspaceId, id)}/upgrade`, post(input)),
  change: (workspaceId: string, id: string, input: ExtensionChange) =>
    request<ExtensionView>(`${item(workspaceId, id)}/enabled`, post(input)),
  remove: (workspaceId: string, id: string, input: ExtensionRemove) =>
    request<ExtensionView>(`${item(workspaceId, id)}/remove`, post(input)),
  importMcp: (workspaceId: string, id: string, input: ExtensionMCPImport) =>
    request<components['schemas']['MCPView']>(`${item(workspaceId, id)}/mcp`, post(input)),
  receipt: (requestId: string) => request<unknown>(`/requests/${encodeURIComponent(requestId)}`),
}
