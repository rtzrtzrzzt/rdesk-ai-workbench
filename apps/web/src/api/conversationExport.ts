import { fetchDownload } from './download'

export type ConversationExportFormat = 'markdown' | 'json'
const exportPath = (threadId: string, format: ConversationExportFormat) =>
  `/threads/${encodeURIComponent(threadId)}/export?format=${format}`

export function conversationExportUrl(threadId: string, format: ConversationExportFormat) {
  return `/api/v1${exportPath(threadId, format)}`
}

export function fetchConversationExport(
  threadId: string,
  format: ConversationExportFormat,
  signal: AbortSignal,
) {
  return fetchDownload(exportPath(threadId, format), {
    contentType: format === 'json' ? 'application/json' : 'text/markdown',
    filename: `conversation-${threadId}.${format === 'json' ? 'json' : 'md'}`,
    maxBytes: 16 * 1024 * 1024,
    limitMessage: '导出超过 16 MiB，请选择较短历史分支。',
    signal,
  })
}
