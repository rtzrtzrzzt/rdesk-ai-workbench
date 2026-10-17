import { request } from './client'
import type { components } from './schema'

/** Seals admission using the server's idempotency lock; a missing receipt alone is insufficient. */
export const closePendingRequest = (id: string) =>
  request<components['schemas']['RequestClosure']>(`/requests/${encodeURIComponent(id)}/close`, {
    method: 'POST',
    body: '{}',
  })
