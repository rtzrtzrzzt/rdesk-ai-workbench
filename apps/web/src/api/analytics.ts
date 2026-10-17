import { request } from './client'
import type { AnalyticsCalls, AnalyticsSummary, HardwareSnapshot } from './types'
export type AnalyticsQueryFilters = {
  window: AnalyticsSummary['filters']['window']
  workspace_id?: string
  model_connection_id?: string
}
function filterQuery(filters: AnalyticsQueryFilters) {
  const query = new URLSearchParams({ window: filters.window })
  if (filters.workspace_id) query.set('workspace_id', filters.workspace_id)
  if (filters.model_connection_id) query.set('model_connection_id', filters.model_connection_id)
  return query
}
export const analyticsApi = {
  summary: (filters: AnalyticsQueryFilters) =>
    request<AnalyticsSummary>(`/analytics/summary?${filterQuery(filters)}`),
  calls: (filters: AnalyticsQueryFilters, limit: number, cursor?: string) => {
    const query = filterQuery(filters)
    query.set('limit', String(limit))
    if (cursor) query.set('cursor', cursor)
    return request<AnalyticsCalls>(`/analytics/calls?${query}`)
  },
  hardware: () => request<HardwareSnapshot>('/analytics/hardware'),
}
