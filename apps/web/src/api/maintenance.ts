import { request } from './client'
import type {
  BackupCreate,
  BackupManifest,
  BackupView,
  MaintenanceAbort,
  RestoreCreate,
  RestoreView,
  DiagnosticsSnapshot,
  CleanupPreview,
  CleanupCreate,
  CleanupResult,
} from './types'
import { fetchDownload } from './download'
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const maintenanceApi = {
  cleanupPreview: () => request<CleanupPreview>('/maintenance/cleanup'),
  cleanup: (input: CleanupCreate) => request<CleanupResult>('/maintenance/cleanup', post(input)),
  diagnostics: () => request<DiagnosticsSnapshot>('/maintenance/diagnostics'),
  exportDiagnostics: (signal: AbortSignal) =>
    fetchDownload('/maintenance/diagnostics/export', {
      contentType: 'application/zip',
      filename: 'workbench-diagnostics.zip',
      maxBytes: 1024 * 1024,
      limitMessage: '诊断导出超过 1 MiB，未交给浏览器保存。',
      signal,
    }),
  backups: () => request<BackupView[]>('/maintenance/backups'),
  manifest: (id: string) =>
    request<BackupManifest>(`/maintenance/backups/${encodeURIComponent(id)}`),
  create: (input: BackupCreate) => request<BackupView>('/maintenance/backups', post(input)),
  restore: (input: RestoreCreate) => request<RestoreView>('/maintenance/restores', post(input)),
  abort: () => request<MaintenanceAbort>('/maintenance/abort', post({})),
}
