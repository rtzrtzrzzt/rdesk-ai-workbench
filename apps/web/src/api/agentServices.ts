import { request } from './client'
import type { RunOptions } from './types'

export type ServiceDefinition = {
  model_connection_id: string
  allowed_tools: string[]
  instructions: string
  runtime_options: RunOptions
  source_workspace_id?: string | null
  source_paths?: string[]
}
export type AgentService = {
  id: string
  name: string
  description: string
  enabled: boolean
  version: number
  revision_id: string
  revision_number: number
  definition: ServiceDefinition
}
export type ServiceCapabilities = {
  models: { id: string; name: string }[]
  tools: { name: string; description: string }[]
  unsupported: string[]
  source_sharing?: {
    mode: 'revision_snapshot'
    read_only: boolean
    explicit_paths_required: boolean
    frozen_at: 'publication'
    max_file_bytes: number
    max_total_bytes: number
    max_files: number
  }
  runtime_options_schema: {
    properties?: Record<string, { default?: unknown; minimum?: number; maximum?: number }>
  }
}
export type ApiClient = {
  id: string
  name: string
  enabled: boolean
  version: number
  requests_per_minute: number
  max_active_runs: number
  created_at: string
}
export type ApiKey = {
  id: string
  prefix: string
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}
export type ApiGrant = {
  id: string
  client_id: string
  service_id: string
  enabled: boolean
  actions: string[]
  allowed_tools: string[] | null
  version: number
}
export type ListenerSettings = {
  enabled: boolean
  host: string
  port: number
  public_hosts: string[]
  ssl_certfile: string | null
  ssl_keyfile: string | null
  version: number
  running: boolean
  url: string | null
  message: string
}

const post = <T>(path: string, input: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(input) })
export const agentServicesApi = {
  list: (signal?: AbortSignal) => request<AgentService[]>('/agent-services', { signal }),
  capabilities: (signal?: AbortSignal) =>
    request<ServiceCapabilities>('/agent-services/capabilities', { signal }),
  publish: (input: Pick<AgentService, 'name' | 'description' | 'definition'>) =>
    post<AgentService>('/agent-services', input),
  revise: (
    id: string,
    input: Pick<AgentService, 'name' | 'description' | 'definition'> & { expected_version: number },
  ) => post<AgentService>(`/agent-services/${id}/revisions`, input),
  state: (service: AgentService) =>
    post<AgentService>(`/agent-services/${service.id}/state`, {
      enabled: !service.enabled,
      expected_version: service.version,
    }),
  clients: (signal?: AbortSignal) => request<ApiClient[]>('/api-clients', { signal }),
  createClient: (input: {
    request_id: string
    name: string
    requests_per_minute: number
    max_active_runs: number
  }) => post<ApiClient>('/api-clients', input),
  updateClient: (
    id: string,
    input: Pick<ApiClient, 'enabled' | 'requests_per_minute' | 'max_active_runs'> & {
      expected_version: number
    },
  ) => post<ApiClient>(`/api-clients/${id}`, input),
  keys: (id: string, signal?: AbortSignal) =>
    request<ApiKey[]>(`/api-clients/${id}/keys`, { signal }),
  issueKey: (id: string, input: { request_id: string; expires_at?: string | null }) =>
    post<ApiKey & { key: string | null; shown_once: boolean }>(`/api-clients/${id}/keys`, input),
  revokeKey: (clientId: string, keyId: string, requestId: string) =>
    post<ApiKey>(`/api-clients/${clientId}/keys/${keyId}/revoke`, { request_id: requestId }),
  grants: (id: string, signal?: AbortSignal) =>
    request<ApiGrant[]>(`/api-clients/${id}/grants`, { signal }),
  saveGrant: (
    clientId: string,
    serviceId: string,
    input: Pick<ApiGrant, 'enabled' | 'actions' | 'allowed_tools'> & { expected_version: number },
  ) => post<ApiGrant>(`/api-clients/${clientId}/grants/${serviceId}`, input),
  listener: (signal?: AbortSignal) => request<ListenerSettings>('/agent-api/listener', { signal }),
  saveListener: (
    input: Omit<ListenerSettings, 'version' | 'running' | 'url' | 'message'> & {
      expected_version: number
    },
  ) => post<ListenerSettings>('/agent-api/listener', input),
  listenerState: (running: boolean, requestId: string) =>
    post<ListenerSettings>(`/agent-api/listener/${running ? 'start' : 'stop'}`, {
      request_id: requestId,
    }),
}
