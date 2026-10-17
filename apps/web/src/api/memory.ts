import { request } from './client'
import type {
  GlobalRules,
  GlobalRulesUpdate,
  MemoryCreate,
  MemoryDocument,
  MemoryDocumentList,
  MemoryForget,
  MemoryForgetResult,
  MemoryProposal,
  MemoryProposalDecision,
  MemorySearchResult,
  MemoryUpdate,
  MemoryRevisionList,
  MemoryRevisionView,
  MemoryRevisionComparison,
  MemoryRestore,
  RulesSnapshot,
} from './types'

const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}`
const documents = (workspaceId: string) => `${root(workspaceId)}/memory/documents`

export const memoryApi = {
  list: (workspaceId: string, scope: 'user' | 'workspace') =>
    request<MemoryDocumentList>(`${documents(workspaceId)}?${new URLSearchParams({ scope })}`),
  document: (workspaceId: string, id: string) =>
    request<MemoryDocument>(`${documents(workspaceId)}/${encodeURIComponent(id)}`),
  revisions: (workspaceId: string, id: string) =>
    request<MemoryRevisionList>(`${documents(workspaceId)}/${encodeURIComponent(id)}/revisions`),
  revision: (workspaceId: string, id: string, version: string) =>
    request<MemoryRevisionView>(
      `${documents(workspaceId)}/${encodeURIComponent(id)}/revisions/${encodeURIComponent(version)}`,
    ),
  compareRevision: (workspaceId: string, id: string, version: string) =>
    request<MemoryRevisionComparison>(
      `${documents(workspaceId)}/${encodeURIComponent(id)}/revisions/${encodeURIComponent(version)}/compare`,
    ),
  restoreRevision: (workspaceId: string, id: string, input: MemoryRestore) =>
    request<MemoryDocument>(
      `${documents(workspaceId)}/${encodeURIComponent(id)}/restore`,
      post(input),
    ),
  create: (workspaceId: string, input: MemoryCreate) =>
    request<MemoryDocument>(documents(workspaceId), post(input)),
  update: (workspaceId: string, id: string, input: MemoryUpdate) =>
    request<MemoryDocument>(`${documents(workspaceId)}/${encodeURIComponent(id)}`, post(input)),
  forget: (workspaceId: string, id: string, input: MemoryForget) =>
    request<MemoryForgetResult>(
      `${documents(workspaceId)}/${encodeURIComponent(id)}/forget`,
      post(input),
    ),
  search: (workspaceId: string, scope: 'user' | 'workspace', q: string) =>
    request<MemorySearchResult>(
      `${root(workspaceId)}/memory/search?${new URLSearchParams({ scope, q })}`,
    ),
  rules: (workspaceId: string, paths: string[]) => {
    const query = new URLSearchParams()
    paths.forEach((path) => query.append('paths', path))
    return request<RulesSnapshot>(`${root(workspaceId)}/rules${query.size ? `?${query}` : ''}`)
  },
  updateGlobalRules: (workspaceId: string, input: GlobalRulesUpdate) =>
    request<GlobalRules>(`${root(workspaceId)}/rules/global`, post(input)),
  proposals: (workspaceId: string, scope: 'user' | 'workspace') =>
    request<MemoryProposal[]>(
      `${root(workspaceId)}/memory/proposals?${new URLSearchParams({ scope })}`,
    ),
  decideProposal: (workspaceId: string, id: string, input: MemoryProposalDecision) =>
    request<MemoryProposal>(
      `${root(workspaceId)}/memory/proposals/${encodeURIComponent(id)}/decisions`,
      post(input),
    ),
}
