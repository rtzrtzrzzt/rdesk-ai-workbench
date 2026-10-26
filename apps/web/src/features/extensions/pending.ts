import type {
  ExtensionChange,
  ExtensionInstall,
  ExtensionMCPImport,
  ExtensionRemove,
  ExtensionSource,
  ExtensionUpgrade,
} from '../../api/extensions'
import { record } from './manifest'

export type PendingOperation =
  | { kind: 'install'; input: ExtensionInstall }
  | { kind: 'upgrade'; id: string; input: ExtensionUpgrade }
  | { kind: 'change'; id: string; input: ExtensionChange }
  | { kind: 'remove'; id: string; input: ExtensionRemove }
  | { kind: 'mcp'; id: string; input: ExtensionMCPImport }

export const operationLabels: Record<PendingOperation['kind'], string> = {
  install: '安装',
  upgrade: '更新',
  change: '修改启用状态',
  remove: '卸载',
  mcp: '导入 MCP 连接',
}

export const pendingKey = (userId: string, workspaceId: string) =>
  `workbench.extensions-pending.${userId}.${workspaceId}`

function sourceValue(value: unknown): ExtensionSource | null {
  const source = record(value)
  if (
    !source ||
    !['directory', 'zip', 'git'].includes(String(source.kind)) ||
    typeof source.location !== 'string' ||
    (source.revision !== undefined &&
      source.revision !== null &&
      typeof source.revision !== 'string') ||
    (source.subdirectory !== undefined && typeof source.subdirectory !== 'string')
  )
    return null
  return {
    kind: source.kind as ExtensionSource['kind'],
    location: source.location,
    revision: typeof source.revision === 'string' ? source.revision : null,
    subdirectory: typeof source.subdirectory === 'string' ? source.subdirectory : '.',
  }
}

export function parsePending(value: unknown): PendingOperation | null {
  const pending = record(value)
  const input = record(pending?.input)
  if (
    !pending ||
    !input ||
    typeof input.request_id !== 'string' ||
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(input.request_id)
  )
    return null
  const request_id = input.request_id
  const source = sourceValue(input.source)
  const digest =
    typeof input.expected_content_digest === 'string' &&
    /^[a-f0-9]{64}$/.test(input.expected_content_digest)
      ? input.expected_content_digest
      : null
  if (pending.kind === 'install') {
    if (
      !source ||
      typeof input.enabled !== 'boolean' ||
      !['user', 'workspace'].includes(String(input.scope)) ||
      !digest
    )
      return null
    return {
      kind: 'install',
      input: {
        source,
        enabled: input.enabled,
        scope: input.scope as 'user' | 'workspace',
        expected_content_digest: digest,
        request_id,
      },
    }
  }
  if (
    typeof pending.id !== 'string' ||
    typeof input.expected_version !== 'number' ||
    !Number.isInteger(input.expected_version) ||
    input.expected_version < 1
  )
    return null
  const id = pending.id
  const versioned = { expected_version: input.expected_version, request_id }
  if (pending.kind === 'remove') return { kind: 'remove', id, input: versioned }
  if (pending.kind === 'upgrade' && source && digest)
    return { kind: 'upgrade', id, input: { ...versioned, source, expected_content_digest: digest } }
  if (
    pending.kind === 'change' &&
    typeof input.enabled === 'boolean' &&
    ['installation', 'workspace'].includes(String(input.scope))
  )
    return {
      kind: 'change',
      id,
      input: {
        ...versioned,
        enabled: input.enabled,
        scope: input.scope as 'installation' | 'workspace',
      },
    }
  if (pending.kind === 'mcp' && typeof input.server_name === 'string')
    return { kind: 'mcp', id, input: { ...versioned, server_name: input.server_name } }
  return null
}

export function readPending(key: string): { pending: PendingOperation | null; error: string } {
  try {
    const stored = localStorage.getItem(key)
    if (!stored) return { pending: null, error: '' }
    const pending = parsePending(JSON.parse(stored))
    return {
      pending,
      error: pending
        ? ''
        : '待核对请求记录无法读取，请保留浏览器记录并联系维护者，当前暂停新操作。',
    }
  } catch {
    return { pending: null, error: '无法读取浏览器中的待核对记录，当前暂停新操作。' }
  }
}
