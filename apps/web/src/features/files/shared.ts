import type { ArtifactView, ProcessFile, ProcessorCapability } from '../../api/types'
export type PendingProcess = { artifactId: string; filename: string; input: ProcessFile }
export const taskKey = (workspaceId: string) => `workbench.file-tasks.${workspaceId}`
export const pendingKey = (workspaceId: string) => `workbench.file-request.${workspaceId}`
export function readTaskIds(workspaceId: string): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(taskKey(workspaceId)) ?? '[]')
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string')
      : []
  } catch {
    return []
  }
}
export function readPending(workspaceId: string): PendingProcess | null {
  try {
    const value = JSON.parse(
      localStorage.getItem(pendingKey(workspaceId)) ?? 'null',
    ) as PendingProcess | null
    return value &&
      typeof value.artifactId === 'string' &&
      typeof value.input?.request_id === 'string'
      ? value
      : null
  } catch {
    return null
  }
}
export const operationLabels: Record<NonNullable<ProcessFile['operation']>, string> = {
  extract: '提取内容',
  preview: '生成安全预览',
  ocr: 'OCR 识别文字',
  convert_pdf: 'Office 转 PDF',
  video_frames: '按时间范围提取视频帧',
  transcribe: '本地语音转录（保留时间戳）',
}
export function operationLabel(operation: string) {
  return (
    Object.entries(operationLabels).find(([key]) => key === operation)?.[1] ??
    (operation === 'export' ? '创建导出' : operation)
  )
}
export function artifactFormat(artifact: ArtifactView) {
  return typeof artifact.metadata.detected_format === 'string'
    ? artifact.metadata.detected_format
    : ''
}
export function isOfficeSource(artifact: ArtifactView) {
  const suffix = artifact.filename.split('.').at(-1)?.toLowerCase() ?? ''
  const detected = artifactFormat(artifact)
  return (
    artifact.status === 'available' &&
    ['docx', 'xlsx', 'pptx', 'odt', 'ods', 'odp'].includes(suffix) &&
    (!detected || detected === suffix || detected === 'zip')
  )
}
export function relevantProcessors(artifact: ArtifactView, capabilities: ProcessorCapability[]) {
  const metadata = artifact.metadata as Record<string, unknown>
  const detected = typeof metadata.detected_format === 'string' ? metadata.detected_format : ''
  const suffix = artifact.filename.split('.').at(-1)?.toLowerCase() ?? ''
  return capabilities.filter(
    (capability) =>
      capability.id === detected ||
      capability.formats.includes(detected) ||
      (!detected && capability.formats.includes(suffix)) ||
      (capability.id === 'office_conversion' && isOfficeSource(artifact)) ||
      (capability.id === 'asr' && /^(audio|video)\//.test(artifact.mime_type)) ||
      (capability.id === 'archive' && detected === 'zip') ||
      (capability.id === 'text' && artifact.mime_type.startsWith('text/')),
  )
}
export function matchingProcessors(artifact: ArtifactView, capabilities: ProcessorCapability[]) {
  return relevantProcessors(artifact, capabilities).filter(
    (capability) => capability.status === 'available',
  )
}
