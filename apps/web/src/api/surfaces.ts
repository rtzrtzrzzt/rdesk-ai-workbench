import { request } from './client'
import type { SurfaceList, UIProtocolStatus } from './types'
export const surfacesApi = {
  list: (runId: string) => request<SurfaceList>(`/runs/${encodeURIComponent(runId)}/surfaces`),
  protocol: () => request<UIProtocolStatus>('/ui/protocol'),
}
