import { request } from './client'
import type { components } from './schema'

export type ConversationHistoryView = components['schemas']['ConversationHistoryView']
export type ConversationHistorySave = components['schemas']['ConversationHistorySave']
export type ConversationHistoryScope = ConversationHistorySave['scope']

export const conversationHistoryApi = {
  settings: (signal?: AbortSignal) =>
    request<ConversationHistoryView>('/settings/conversation-history', { signal }),
  save: (input: ConversationHistorySave) =>
    request<ConversationHistoryView>('/settings/conversation-history', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
}
