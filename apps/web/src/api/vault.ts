import { request } from './client'
import type { RequestResult, VaultCommand, VaultInitialize, VaultUnlock, VaultView } from './types'
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const vaultApi = {
  status: () => request<VaultView>('/vault/status'),
  initialize: (input: VaultInitialize) => request<VaultView>('/vault/initialize', post(input)),
  unlock: (input: VaultUnlock) => request<VaultView>('/vault/unlock', post(input)),
  lock: (input: VaultCommand) => request<VaultView>('/vault/lock', post(input)),
  migrate: (input: VaultCommand) => request<VaultView>('/vault/migrate-legacy', post(input)),
  receipt: (requestId: string) =>
    request<RequestResult>(`/requests/${encodeURIComponent(requestId)}`),
}
