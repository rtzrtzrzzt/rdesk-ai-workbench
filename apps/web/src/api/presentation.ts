import { request } from './client'
import type { components } from './schema'

export type RunPresentation = components['schemas']['PresentedRun']

export const presentationApi = {
  thread: (threadId: string, signal?: AbortSignal) =>
    request<{ groups: RunPresentation[]; last_event_seq?: number }>(
      `/threads/${threadId}/presentation`,
      { signal },
    ),
}
