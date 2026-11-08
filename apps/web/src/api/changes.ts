import { request } from './client'

export type ChangesCoverage = 'pending' | 'complete' | 'partial' | 'unknown'
export type RunChanges = {
  revision: string
  coverage: ChangesCoverage
  gaps: string[]
  files: {
    id: string
    path: string
    kind: string
    added_lines: number | null
    removed_lines: number | null
    before_version: string | null
    after_version: string | null
    attribution: string
  }[]
  totals: { files: number; added_lines: number | null; removed_lines: number | null }
}
export const changesApi = {
  run: (threadId: string, runId: string, signal?: AbortSignal) =>
    request<RunChanges>(`/threads/${threadId}/changes?run_id=${encodeURIComponent(runId)}`, {
      signal,
    }),
  detail: (threadId: string, changeId: string, runId: string, signal?: AbortSignal) =>
    request<{ path: string; diff: string | null; truncated: boolean; coverage: string }>(
      `/threads/${threadId}/changes/${encodeURIComponent(changeId)}?run_id=${encodeURIComponent(runId)}`,
      { signal },
    ),
}
