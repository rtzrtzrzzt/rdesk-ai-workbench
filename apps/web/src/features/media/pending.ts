import type { MediaCreate, MediaReference } from '../../api/media'
import { record } from './shared'

export const generationKey = (userId: string, workspaceId: string) =>
  `workbench.media-pending.${userId}.${workspaceId}`

export function readPendingGeneration(key: string): { input: MediaCreate | null; error: string } {
  try {
    const stored = localStorage.getItem(key)
    if (!stored) return { input: null, error: '' }
    const value = record(JSON.parse(stored))
    if (
      !value ||
      typeof value.request_id !== 'string' ||
      !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value.request_id) ||
      !['image', 'video', 'audio', '3d'].includes(String(value.capability)) ||
      typeof value.prompt !== 'string' ||
      typeof value.connection_id !== 'string'
    )
      throw new Error('invalid pending generation')
    const references: MediaReference[] = []
    if (Array.isArray(value.references)) {
      for (const reference of value.references) {
        const item = record(reference)
        if (
          !item ||
          typeof item.artifact_id !== 'string' ||
          typeof item.version !== 'string' ||
          (item.consent_remote !== undefined && typeof item.consent_remote !== 'boolean')
        )
          throw new Error('invalid pending reference')
        references.push({
          artifact_id: item.artifact_id,
          version: item.version,
          consent_remote: item.consent_remote === true,
        })
      }
    }
    const numeric: Record<string, number | null | undefined> = {}
    for (const field of [
      'width',
      'height',
      'seed',
      'steps',
      'duration_seconds',
      'expected_connection_version',
    ]) {
      const item = value[field]
      if (
        item !== undefined &&
        item !== null &&
        (typeof item !== 'number' || !Number.isFinite(item))
      )
        throw new Error('invalid pending numeric value')
      numeric[field] = typeof item === 'number' ? item : undefined
    }
    return {
      input: {
        request_id: value.request_id,
        capability: value.capability as NonNullable<MediaCreate['capability']>,
        prompt: value.prompt,
        connection_id: value.connection_id,
        references,
        width: numeric.width ?? undefined,
        height: numeric.height ?? undefined,
        seed: numeric.seed,
        steps: numeric.steps,
        duration_seconds: numeric.duration_seconds,
        expected_connection_version: numeric.expected_connection_version,
        ...(typeof value.voice === 'string' ? { voice: value.voice } : {}),
        ...(typeof value.format === 'string' ? { format: value.format } : {}),
      },
      error: '',
    }
  } catch {
    return {
      input: null,
      error: '待核对生成请求无法读取，当前暂停新生成。请保留浏览器记录并联系维护者。',
    }
  }
}
