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
const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/search`
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const searchApi = {
  connection: (workspaceId: string, scope: SearchConnectionSave['scope']) =>
    request<SearchConnectionView | null>(
      `${root(workspaceId)}/connection?${new URLSearchParams({ scope })}`,
    ),
  save: (workspaceId: string, input: SearchConnectionSave) =>
    request<SearchConnectionView>(`${root(workspaceId)}/connection`, post(input)),
  capability: (workspaceId: string) => request<SearchCapability>(`${root(workspaceId)}/capability`),
  check: (workspaceId: string) => request<SearchCapability>(`${root(workspaceId)}/check`, post({})),
  query: (workspaceId: string, input: WebSearchQuery) =>
    request<WebSearchResult>(`${root(workspaceId)}/query`, post(input)),
  read: (workspaceId: string, input: FetchPage) =>
    request<WebPage>(`${root(workspaceId)}/read`, post(input)),
}
