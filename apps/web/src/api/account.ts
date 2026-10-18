import { request } from './client'
import type { AccountSecurity } from './types'
export const accountApi = {
  security: () => request<AccountSecurity>('/auth/security'),
  activity: () => request<void>('/auth/activity', { method: 'POST', body: JSON.stringify({}) }),
}
