import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, authGeneration } from '../../api/client'
import { settingsApi } from '../../api/settings'
import type { ExperienceSave, ExperienceValues, ExperienceView } from '../../api/settings'

type Pending = {
  command: ExperienceSave
  initializing: boolean
  changes?: Partial<ExperienceValues>
}
type Editor = {
  identity: string
  draft: Partial<ExperienceValues> | null
  pending: Pending | null
  busy: boolean
  conflict: boolean
  message: string
}
const fallback: ExperienceValues = { theme: 'system', input_mode: 'queue' }
const pendingKey = (userId: string) => `workbench.experience.pending:${userId}`
const themeValid = (value: unknown): value is ExperienceValues['theme'] =>
  value === 'system' || value === 'light' || value === 'dark'
const modeValid = (value: unknown): value is ExperienceValues['input_mode'] =>
  value === 'queue' || value === 'steer'

function legacyCandidate(): ExperienceValues {
  try {
    const theme = localStorage.getItem('workbench.theme')
    const mode = localStorage.getItem('workbench.input-mode')
    return {
      theme: themeValid(theme) ? theme : 'system',
      input_mode: modeValid(mode) ? mode : 'queue',
    }
  } catch {
    return fallback
  }
}

function restorePending(userId: string): Pending | false | null {
  try {
    const raw = sessionStorage.getItem(pendingKey(userId))
    if (raw === null) return null
    const value = JSON.parse(raw) as Pending | null
    if (
      value &&
      typeof value.initializing === 'boolean' &&
      value.command &&
      typeof value.command.request_id === 'string' &&
      /^[0-9a-f-]{36}$/i.test(value.command.request_id) &&
      Number.isInteger(value.command.expected_version) &&
      value.command.expected_version >= 0 &&
      themeValid(value.command.theme) &&
      modeValid(value.command.input_mode)
    )
      return {
        initializing: value.initializing,
        changes: value.changes && {
          ...(themeValid(value.changes.theme) ? { theme: value.changes.theme } : {}),
          ...(modeValid(value.changes.input_mode) ? { input_mode: value.changes.input_mode } : {}),
        },
        command: {
          request_id: value.command.request_id,
          expected_version: value.command.expected_version,
          theme: value.command.theme,
          input_mode: value.command.input_mode,
        },
      }
  } catch {
    // An unreadable recovery record must not trigger an automatic submission.
  }
  return false
}

/** Account values are authoritative; uncertain writes retain their exact identity. */
export function useExperiencePreferences(userId: string | undefined, inputLocked: boolean) {
  const client = useQueryClient()
  const generation = authGeneration()
  const identity = `${userId ?? ''}:${generation}`
  const owner = useRef(identity)
  owner.current = identity
  const key = ['experience-preferences', userId, generation] as const
  const [editor, setEditor] = useState<Editor>({
    identity,
    draft: null,
    pending: null,
    busy: false,
    conflict: false,
    message: '',
  })
  const pending = useRef<Pending | null>(null)
  const inFlight = useRef(false)
  const initializedAttempt = useRef(false)
  const [appliedMode, setAppliedMode] = useState({ identity, value: fallback.input_mode })
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => settingsApi.experience(signal),
    enabled: !!userId,
    retry: false,
    refetchInterval: 4000,
  })
  const snapshot = userId ? query.data : undefined
  const current: Editor =
    editor.identity === identity
      ? editor
      : { identity, draft: null, pending: null, busy: false, conflict: false, message: '' }
  const saved = snapshot?.initialized ? snapshot : undefined
  const inputMode = appliedMode.identity === identity ? appliedMode.value : fallback.input_mode

  function sameOwner() {
    return owner.current === identity && authGeneration() === generation
  }
  function patch(change: Partial<Editor>) {
    if (sameOwner()) setEditor((old) => ({ ...old, ...change, identity }))
  }
  function store(value: Pending | null) {
    if (!userId) return
    if (value) sessionStorage.setItem(pendingKey(userId), JSON.stringify(value))
    else sessionStorage.removeItem(pendingKey(userId))
    pending.current = value
    patch({ pending: value })
  }

  useEffect(() => {
    const restoration = userId ? restorePending(userId) : null
    const restored = restoration || null
    pending.current = restored
    inFlight.current = false
    initializedAttempt.current = restoration !== null
    setEditor({
      identity,
      draft: restored
        ? (restored.changes ?? {
            theme: restored.command.theme,
            input_mode: restored.command.input_mode,
          })
        : null,
      pending: restored,
      busy: false,
      conflict: false,
      message: restored
        ? '上次保存结果待核对，请核对本次保存。'
        : restoration === false
          ? '上次保存的本机记录无法读取，请先核对账户当前设置。'
          : '',
    })
  }, [identity, userId])

  useEffect(() => {
    document.documentElement.dataset.theme = saved?.theme ?? 'system'
  }, [saved?.theme, identity])

  useEffect(() => {
    if (saved && !inputLocked) setAppliedMode({ identity, value: saved.input_mode })
  }, [saved?.input_mode, inputLocked, identity])

  async function refresh() {
    const result = await query.refetch()
    if (!sameOwner()) return undefined
    if (result.isError || !result.data) throw result.error ?? new Error('账户偏好读取未完成。')
    return result.data
  }

  async function execute(value: Pending) {
    if (!userId || inFlight.current || !sameOwner()) return
    if (pending.current && pending.current.command.request_id !== value.command.request_id) return
    try {
      store(value)
    } catch {
      patch({ message: '无法保留保存请求，本次尚未提交。' })
      return
    }
    inFlight.current = true
    patch({ busy: true, message: '', conflict: false })
    try {
      const receipt = await settingsApi.saveExperience(value.command)
      if (!sameOwner()) return
      await client.cancelQueries({ queryKey: key })
      if (!sameOwner()) return
      client.setQueryData<ExperienceView>(key, (old) =>
        old && old.version > receipt.version ? old : receipt,
      )
      store(null)
      patch({ draft: null, message: '账户偏好已保存，其他浏览器会同步更新。' })
      try {
        const latest = await refresh()
        if (latest && latest.version > receipt.version)
          patch({ message: '本次保存回执已核对；账户偏好已有更新，当前按最新设置生效。' })
      } catch {
        patch({ message: '保存回执已收到，暂未读取最新账户状态；稍后会继续同步。' })
      }
    } catch (error) {
      if (!sameOwner()) return
      const certain = error instanceof ApiError && error.status < 500 && error.status !== 408
      if (certain) {
        try {
          store(null)
        } catch {
          pending.current = null
        }
        const conflict = error.status === 409 || error.status === 410
        patch({
          pending: null,
          conflict,
          draft: value.initializing
            ? null
            : (value.changes ?? {
                theme: value.command.theme,
                input_mode: value.command.input_mode,
              }),
          message: conflict ? '账户偏好已变化，请读取最新设置后核对保留的修改。' : error.message,
        })
        if (conflict) {
          try {
            const latest = await refresh()
            if (value.initializing && latest?.initialized)
              patch({ conflict: false, message: '已使用账户现有偏好，本机旧偏好未覆盖账户设置。' })
          } catch {
            patch({ message: '账户偏好已变化，最新状态暂未读取，请稍后重新读取。' })
          }
        }
      } else {
        patch({ message: '保存结果尚未确认，请核对本次保存；核对会复用同一请求。' })
      }
    } finally {
      if (sameOwner()) {
        inFlight.current = false
        patch({ busy: false })
      }
    }
  }

  useEffect(() => {
    if (
      userId &&
      snapshot &&
      !snapshot.initialized &&
      !initializedAttempt.current &&
      !pending.current
    ) {
      initializedAttempt.current = true
      void execute({
        command: { ...legacyCandidate(), expected_version: 0, request_id: crypto.randomUUID() },
        initializing: true,
      })
    }
    // Initialization is once per account/session, never a reaction to later polls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot?.initialized, identity])

  async function change(change: Partial<ExperienceValues>) {
    if (!saved || inFlight.current || pending.current || current.conflict) return
    if (inputLocked && change.input_mode !== undefined) return
    const changes = { ...current.draft, ...change }
    const values = { theme: saved.theme, input_mode: saved.input_mode, ...changes }
    patch({ draft: changes })
    await execute({
      command: { ...values, expected_version: saved.version, request_id: crypto.randomUUID() },
      initializing: false,
      changes,
    })
  }

  return {
    inputMode,
    values: { ...(saved ?? fallback), ...current.draft },
    ready: !!saved,
    uninitialized: !!snapshot && !snapshot.initialized,
    busy: current.busy,
    pending: !!current.pending,
    draft: !!current.draft,
    conflict: current.conflict,
    message: current.message || (query.isError ? '账户偏好读取未完成，正在等待连接恢复。' : ''),
    inputLocked,
    deferred: !!saved && inputLocked && saved.input_mode !== inputMode,
    change,
    retry: () => pending.current && execute(pending.current),
    initialize: () =>
      !snapshot?.initialized &&
      execute({
        command: { ...legacyCandidate(), expected_version: 0, request_id: crypto.randomUUID() },
        initializing: true,
      }),
    saveDraft: () => change({}),
    reload: async () => {
      if (inFlight.current || pending.current) return
      patch({ busy: true })
      try {
        await refresh()
        patch({
          conflict: false,
          message: current.draft ? '已读取最新设置，保留的修改尚未保存。' : '',
        })
      } catch (error) {
        patch({ message: error instanceof Error ? error.message : '读取未完成。' })
      } finally {
        patch({ busy: false })
      }
    },
    discardDraft: () => {
      if (!inFlight.current && !pending.current)
        patch({ draft: null, conflict: false, message: '' })
    },
  }
}

export type ExperiencePreferences = ReturnType<typeof useExperiencePreferences>
