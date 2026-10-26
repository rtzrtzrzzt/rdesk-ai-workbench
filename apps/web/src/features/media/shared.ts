import type { MediaDriver, MediaKind } from '../../api/media'

export const capabilities: { id: MediaKind; label: string; noun: string; placeholder: string }[] = [
  { id: 'image', label: '生图', noun: '图像', placeholder: '描述想生成的画面、风格和细节…' },
  { id: 'video', label: '视频', noun: '视频', placeholder: '描述场景、动作和镜头变化…' },
  { id: 'audio', label: '音频', noun: '音频', placeholder: '描述声音、音乐或需要生成的语音内容…' },
  { id: '3d', label: '3D', noun: '3D', placeholder: '描述物体、材质和需要的三维细节…' },
]
export const drivers: { id: MediaDriver; label: string; capabilities: MediaKind[] }[] = [
  { id: 'flux3_native', label: 'FLUX3 本机生成', capabilities: ['image'] },
  { id: 'openai_images', label: 'OpenAI Images API', capabilities: ['image'] },
  {
    id: 'media_http',
    label: '通用生成服务 · workbench-media/1',
    capabilities: ['image', 'video', 'audio', '3d'],
  },
]
export const taskTerminal = new Set(['succeeded', 'failed', 'cancelled'])
const statusLabels: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  waiting_resource: '等待资源',
  cancelling: '正在停止',
  succeeded: '已完成',
  failed: '未完成',
  cancelled: '已停止',
  outcome_unknown: '结果待核对',
  paused: '已暂停',
  awaiting_approval: '等待授权',
}
const phaseLabels: Record<string, string> = {
  expanding_prompt: '优化提示词',
  kernels: '准备生成环境',
  queued: '等待开始',
  waiting_resource: '等待可用资源',
  encode: '编码',
  text: '编码提示词',
  reference: '编码参考图像',
  references: '编码参考图像',
  preparing_references: '准备参考图像',
  sample: '采样生成',
  sampling: '采样生成',
  decode: '解码产物',
  publish: '保存产物',
  submitting: '提交生成请求',
  polling: '等待生成服务',
  generating: '生成中',
  complete: '完成',
  completed: '完成',
  cancelled: '已停止',
}
export const taskStatus = (value: string) => statusLabels[value] ?? value
export const taskPhase = (value: string) => phaseLabels[value] ?? value
export const describeError = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请稍后重试。'
export function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function nativeSuggestion(value: unknown) {
  const suggestion = record(record(value)?.suggested)
  return suggestion &&
    suggestion.driver === 'flux3_native' &&
    suggestion.capability === 'image' &&
    typeof suggestion.model_directory === 'string' &&
    suggestion.model_directory &&
    typeof suggestion.model_identifier === 'string' &&
    suggestion.model_identifier
    ? { model_directory: suggestion.model_directory, model_identifier: suggestion.model_identifier }
    : null
}
