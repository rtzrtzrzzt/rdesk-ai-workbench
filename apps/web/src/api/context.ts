import { request } from './client'

/** Conversation context includes terminal tasks and conversations without a run. */
export type ContextView = {
  thread_id: string
  used_tokens: number | null
  context_window: number | null
  remaining_tokens: number | null
  source: 'measured' | 'tokenizer' | 'estimated' | 'unavailable'
  updated_at: string | null
  last_compaction_at: string | null
  last_compaction_before_tokens: number | null
  last_compaction_after_tokens: number | null
  compaction_pending: boolean
  compaction_status: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed'
  compaction_error: string | null
  active_run_id: string | null
}

const root = (id: string) => `/threads/${encodeURIComponent(id)}/context`
export const contextApi = {
  view: (id: string, modelId?: string, signal?: AbortSignal) =>
    request<ContextView>(
      `${root(id)}${modelId ? `?model_connection_id=${encodeURIComponent(modelId)}` : ''}`,
      { signal },
    ),
  compact: (id: string, request_id: string, model_connection_id?: string) =>
    request<ContextView>(`${root(id)}/compact`, {
      method: 'POST',
      body: JSON.stringify({ request_id, ...(model_connection_id ? { model_connection_id } : {}) }),
    }),
}
