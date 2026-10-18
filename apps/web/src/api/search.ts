import { request } from './client'
import type {
  FetchPage,
  SearchCapability,
  SearchConnectionSave,
  SearchConnectionView,
  WebPage,
  WebSearchQuery,
  WebSearchResult,
} from './types'
export type SearchEngineCatalog = {
  endpoint: string
  fetched_at: string
  engines: { name: string; categories: string[]; default_enabled: boolean }[]
}
export type SearchEngineCatalogQuery = {
  scope?: SearchConnectionSave['scope']
  endpoint?: string
  timeout_seconds?: number
}
const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/search`
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const searchApi = {
  connection: (workspaceId: string, scope: SearchConnectionSave['scope']) =>
    request<SearchConnectionView | null>(
      `${root(workspaceId)}/connection?${new URLSearchParams({ scope })}`,
    ),
  engines: (workspaceId: string, input: SearchEngineCatalogQuery) =>
    request<SearchEngineCatalog>(`${root(workspaceId)}/engines`, post(input)),
  save: (workspaceId: string, input: SearchConnectionSave) =>
    request<SearchConnectionView>(`${root(workspaceId)}/connection`, post(input)),
  capability: (workspaceId: string) => request<SearchCapability>(`${root(workspaceId)}/capability`),
  check: (workspaceId: string) => request<SearchCapability>(`${root(workspaceId)}/check`, post({})),
  query: (workspaceId: string, input: WebSearchQuery) =>
    request<WebSearchResult>(`${root(workspaceId)}/query`, post(input)),
  read: (workspaceId: string, input: FetchPage) =>
    request<WebPage>(`${root(workspaceId)}/read`, post(input)),
}
