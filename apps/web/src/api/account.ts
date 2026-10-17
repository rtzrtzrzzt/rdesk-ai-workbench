import { request } from './client'
import type { AccountSecurity, PasswordChange, PasswordChangeReceipt } from './types'
export const accountApi = {
  security: () => request<AccountSecurity>('/auth/security'),
  activity: () => request<void>('/auth/activity', { method: 'POST', body: JSON.stringify({}) }),
  changePassword: (input: PasswordChange) =>
    request<PasswordChangeReceipt>('/auth/password', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
}
