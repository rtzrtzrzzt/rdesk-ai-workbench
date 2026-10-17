import { request } from './client'
import type {
  ArtifactView,
  ArtifactInputPlan,
  ExportCreate,
  ExportCapability,
  FilePreview,
  FileTask,
  ProcessFile,
  ProcessorCapability,
  UploadCreate,
  UploadStatus,
} from './types'
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}`
const artifactPath = (workspaceId: string, artifactId: string) =>
  `${root(workspaceId)}/artifacts/${encodeURIComponent(artifactId)}`
export const filesApi = {
  inputPlan: (workspaceId: string, modelId: string, ids: string[]) =>
    request<ArtifactInputPlan[]>(
      `${root(workspaceId)}/input-plan`,
      post({ model_connection_id: modelId, artifact_ids: ids }),
    ),
  exportCapabilities: (workspaceId: string) =>
    request<ExportCapability[]>(`${root(workspaceId)}/export-capabilities`),
  export: (workspaceId: string, input: ExportCreate) =>
    request<FileTask>(`${root(workspaceId)}/exports`, post(input)),
  list: (workspaceId: string) => request<ArtifactView[]>(`${root(workspaceId)}/artifacts`),
  artifact: (workspaceId: string, id: string) =>
    request<ArtifactView>(artifactPath(workspaceId, id)),
  upload: (workspaceId: string, input: UploadCreate) =>
    request<UploadStatus>(`${root(workspaceId)}/uploads`, post(input)),
  uploadStatus: (workspaceId: string, id: string) =>
    request<UploadStatus>(`${root(workspaceId)}/uploads/${encodeURIComponent(id)}`),
  chunk: (workspaceId: string, id: string, offset: number, body: Blob) =>
    request<UploadStatus>(
      `${root(workspaceId)}/uploads/${encodeURIComponent(id)}/chunks?offset=${offset}`,
      { method: 'POST', body, headers: { 'Content-Type': 'application/octet-stream' } },
    ),
  complete: (workspaceId: string, id: string) =>
    request<ArtifactView>(
      `${root(workspaceId)}/uploads/${encodeURIComponent(id)}/complete`,
      post({}),
    ),
  preview: (workspaceId: string, id: string) =>
    request<FilePreview>(`${artifactPath(workspaceId, id)}/preview`),
  process: (workspaceId: string, id: string, input: ProcessFile) =>
    request<FileTask>(`${artifactPath(workspaceId, id)}/process`, post(input)),
  tasks: (workspaceId: string) => request<FileTask[]>(`${root(workspaceId)}/file-tasks`),
  task: (workspaceId: string, id: string) =>
    request<FileTask>(`${root(workspaceId)}/file-tasks/${encodeURIComponent(id)}`),
  cancel: (workspaceId: string, id: string, requestId: string) =>
    request<FileTask>(
      `${root(workspaceId)}/file-tasks/${encodeURIComponent(id)}/cancel`,
      post({ request_id: requestId }),
    ),
  processors: (workspaceId: string) =>
    request<ProcessorCapability[]>(`${root(workspaceId)}/file-processors`),
  previewImageUrl: (workspaceId: string, id: string) =>
    `/api/v1${artifactPath(workspaceId, id)}/content?download=false`,
  downloadUrl: (workspaceId: string, id: string) =>
    `/api/v1${artifactPath(workspaceId, id)}/content?download=true`,
}
