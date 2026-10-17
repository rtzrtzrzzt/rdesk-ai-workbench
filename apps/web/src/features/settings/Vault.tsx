import { useSettingsAttention } from './SettingsGroup'
import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { vaultApi } from '../../api/vault'
import { closePendingRequest } from '../../api/requests'
import type {
  RequestResult,
  VaultCommand,
  VaultInitialize,
  VaultUnlock,
  VaultView,
} from '../../api/types'
import { Button, Status } from '../../components/ui'
import './Vault.css'

type Action = 'initialize' | 'unlock' | 'lock' | 'migrate-legacy'
type Command =
  | { action: 'initialize'; input: VaultInitialize }
  | { action: 'unlock'; input: VaultUnlock }
  | { action: 'lock' | 'migrate-legacy'; input: VaultCommand }
type PendingMetadata = { action: Action; requestId: string }
const key = (userId: string) => `workbench.vault-pending.${userId}`
const stateLabels: Record<VaultView['state'], string> = {
  uninitialized: '尚未初始化',
  locked: '已锁定',
  unlocked: '已解锁',
  unavailable: '当前不可用',
}
const actionLabels: Record<Action, string> = {
  initialize: '初始化',
  unlock: '解锁',
  lock: '锁定',
  'migrate-legacy': '迁移旧凭据',
}
export function passphraseBytes(value: string) {
  return new TextEncoder().encode(value).length
}
export function validVaultPassphrase(value: string) {
  const length = passphraseBytes(value)
  return length >= 12 && length <= 1024
}
function readPending(userId: string): PendingMetadata | null {
  try {
    const value = JSON.parse(localStorage.getItem(key(userId)) ?? 'null') as PendingMetadata | null
    return value && Object.hasOwn(actionLabels, value.action) && typeof value.requestId === 'string'
      ? { action: value.action, requestId: value.requestId }
      : null
  } catch {
    return null
  }
}
function isVault(value: RequestResult): value is VaultView {
  return value !== null && 'state' in value && 'keyring_available' in value
}
function errorCode(error: unknown) {
  if (
    !(error instanceof ApiError) ||
    !error.body ||
    typeof error.body !== 'object' ||
    !('error' in error.body)
  )
    return ''
  const detail = error.body.error
  return detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
    ? detail.code
    : ''
}
function View({ status }: { status: VaultView }) {
  return (
    <>
      <div className="run-line">
        <h2>密钥库</h2>
        <Status
          tone={
            status.state === 'unlocked'
              ? 'good'
              : status.state === 'unavailable'
                ? 'warning'
                : 'neutral'
          }
        >
          {stateLabels[status.state]}
        </Status>
      </div>
      <dl className="run-meta">
        <dt>保护方式</dt>
        <dd>
          {status.backend === 'keyring'
            ? '系统原生 Keyring'
            : status.backend === 'passphrase'
              ? '独立口令'
              : '尚无'}
        </dd>
        <dt>原生 Keyring</dt>
        <dd>
          {status.keyring_available ? '当前可用' : '当前不可用'}
          {status.keyring_backend ? ` · ${status.keyring_backend}` : ''}
        </dd>
        <dt>初始化</dt>
        <dd>
          {status.initialized
            ? status.initialization_complete
              ? '已完成'
              : '现有记录待核对，不会重建 master'
            : '尚未初始化'}
        </dd>
        <dt>解锁期限</dt>
        <dd>{Math.ceil(status.ttl_seconds)} 秒</dd>
        <dt>剩余时间</dt>
        <dd>
          {status.remaining_seconds === null
            ? '当前没有已解锁期限'
            : `${Math.ceil(status.remaining_seconds)} 秒（服务器最近读数）`}
        </dd>
        <dt>旧式凭据</dt>
        <dd>{status.legacy_credentials}</dd>
        {status.last_lock_reason && (
          <>
            <dt>最近锁定原因</dt>
            <dd>{status.last_lock_reason}</dd>
          </>
        )}
      </dl>
      {status.keyring_reason && <p className="page-note">Keyring：{status.keyring_reason}</p>}
      {status.reason && (
        <p role="status" className="form-error">
          {status.reason}
        </p>
      )}
    </>
  )
}
export function Vault({ userId }: { userId: string }) {
  const query = useQuery({
    queryKey: ['vault-status', userId],
    queryFn: vaultApi.status,
    refetchInterval: 5000,
  })
  const [mode, setMode] = useState<'keyring' | 'passphrase'>('keyring')
  const [passphrase, setPassphrase] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState<PendingMetadata | null>(() => readPending(userId))
  const command = useRef<Command | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const [reconciled, setReconciled] = useState(false)
  useSettingsAttention('vault', !!pending, '密钥库请求待核对')
  useSettingsAttention(
    'vault-error',
    query.isError || !!query.data?.reason || failure,
    '密钥库状态或操作需要处理',
  )
  const status = query.data
  const blocked = busy || !!pending || query.isError
  const needsInitPassphrase =
    status?.state === 'uninitialized' && !status.initialized && mode === 'passphrase'
  const needsUnlockPassphrase = status?.state === 'locked' && status.backend === 'passphrase'
  function store(value: PendingMetadata | null) {
    if (value) localStorage.setItem(key(userId), JSON.stringify(value))
    else localStorage.removeItem(key(userId))
    setPending(value)
  }
  async function post(value: Command) {
    switch (value.action) {
      case 'initialize':
        return vaultApi.initialize(value.input)
      case 'unlock':
        return vaultApi.unlock(value.input)
      case 'lock':
        return vaultApi.lock(value.input)
      case 'migrate-legacy':
        return vaultApi.migrate(value.input)
    }
  }
  async function execute(value: Command) {
    if (busy) return
    if (
      value.action === 'initialize' &&
      (query.data?.initialized || query.data?.state !== 'uninitialized')
    ) {
      setMessage('现有密钥库不会重新初始化。请先读取状态与本次回执，必要时明确解锁。')
      return
    }
    command.current = value
    try {
      store({ action: value.action, requestId: value.input.request_id })
    } catch {
      command.current = null
      setFailure(true)
      setMessage('无法保留非秘密请求标识，本次未提交。')
      return
    }
    setBusy(true)
    setFailure(false)
    setMessage('')
    setReconciled(false)
    setPassphrase('')
    setConfirmation('')
    try {
      await post(value)
      store(null)
      command.current = null
      setMessage(`${actionLabels[value.action]}回执已收到，当前状态以服务端重新读取为准。`)
    } catch (error) {
      setFailure(true)
      const code = errorCode(error)
      const uncertain =
        !(error instanceof ApiError && error.status < 500) ||
        code.includes('unconfirmed') ||
        code.includes('outcome_unknown')
      if (!uncertain) {
        store(null)
        command.current = null
      }
      setMessage(
        `${error instanceof Error ? error.message : '密钥库操作未完成'}${uncertain ? ' 结果未确认，原敏感请求仅保留内存；请先只读核对，不能修改口令或请求标识。' : ''}`,
      )
    } finally {
      await query.refetch()
      setBusy(false)
    }
  }
  function start(action: Action) {
    if (blocked || !status) return
    const request_id = crypto.randomUUID()
    if (action === 'initialize') {
      if (status.initialized || status.state !== 'uninitialized') return
      if (
        mode === 'passphrase' &&
        (!validVaultPassphrase(passphrase) || passphrase !== confirmation)
      ) {
        setMessage('独立口令须为 12–1024 UTF-8 字节，且两次输入完全一致。')
        return
      }
      void execute({
        action,
        input: mode === 'passphrase' ? { mode, passphrase, request_id } : { mode, request_id },
      })
    } else if (action === 'unlock') {
      if (status.state !== 'locked' || !status.initialized) return
      if (status.backend === 'passphrase' && !validVaultPassphrase(passphrase)) {
        setMessage('请输入本密钥库的独立口令，长度为 12–1024 UTF-8 字节。')
        return
      }
      void execute({
        action,
        input: status.backend === 'passphrase' ? { passphrase, request_id } : { request_id },
      })
    } else if (action === 'lock') {
      if (status.state === 'unlocked') void execute({ action, input: { request_id } })
    } else if (status.state === 'unlocked' && status.legacy_credentials > 0)
      void execute({ action, input: { request_id } })
  }
  async function reconcile() {
    if (!pending || busy) return
    const previous = pending
    setBusy(true)
    setFailure(false)
    setMessage('')
    setReconciled(false)
    const [current, receipt] = await Promise.allSettled([
      query.refetch(),
      vaultApi.receipt(previous.requestId),
    ])
    const currentRead = current.status === 'fulfilled' && !current.value.isError
    if (currentRead) setReconciled(true)
    if (receipt.status === 'fulfilled' && isVault(receipt.value)) {
      store(null)
      command.current = null
      setMessage('本次密钥库操作已有持久回执；当前状态按刚读取的服务端结果显示，未重复执行。')
    } else {
      const reason =
        receipt.status === 'rejected' && receipt.reason instanceof Error
          ? receipt.reason.message
          : '本次回执尚不可确认'
      setMessage(
        `${reason}。${currentRead ? '当前状态已读取。' : '状态读取未完成。'}不据此生成替代 master，不自动重发敏感请求。`,
      )
    }
    setBusy(false)
  }
  const canAdopt =
    !!pending &&
    reconciled &&
    !!status &&
    (pending.action === 'initialize'
      ? status.initialized
      : pending.action === 'lock'
        ? status.state === 'locked'
        : pending.action === 'unlock'
          ? status.state === 'unlocked'
          : status.legacy_credentials === 0)
  async function closePending() {
    if (!pending || busy) return
    setBusy(true)
    try {
      const result = await closePendingRequest(pending.requestId)
      if (result.status === 'sealed') {
        store(null)
        command.current = null
        setReconciled(false)
        setMessage('此请求已停止接收，未重发口令。请按重新读取的状态操作；这不代表此前效果已回滚。')
        await query.refetch()
      } else setMessage('本次请求已有记录，请读取状态与回执后核对。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '请求关闭未确认。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="settings-vault">
      <div className="vault-heading-actions">
        <Button disabled={busy || query.isFetching} onClick={() => void query.refetch()}>
          读取密钥库状态
        </Button>
      </div>
      {status ? (
        <View status={status} />
      ) : (
        <>
          <h2>密钥库</h2>
          <p className="page-note">正在读取真实状态…</p>
        </>
      )}
      {query.isError && (
        <p role="alert" className="form-error">
          {query.error.message}
        </p>
      )}
      <p className="page-note">
        模型/API 与 MCP
        凭据由密钥库保护。不会自动初始化、解锁或降级；不需要密钥的本地模型可继续使用。
      </p>
      {pending && (
        <div className="memory-conflict">
          <strong>{actionLabels[pending.action]}请求待核对</strong>
          <p>
            请求标识：{pending.requestId}。仅这个非秘密标识会保留，口令与 master
            不写入浏览器存储或日志。
          </p>
          <div className="button-row">
            <Button disabled={busy} onClick={() => void reconcile()}>
              先读取状态与本次回执
            </Button>
            <Button disabled={busy} onClick={() => void closePending()}>
              关闭未受理请求
            </Button>
            {reconciled &&
              command.current &&
              !(pending.action === 'initialize' && status?.initialized) && (
                <Button
                  disabled={busy}
                  onClick={() => {
                    const original = command.current
                    if (original) void execute(original)
                  }}
                >
                  原样核对提交（同标识）
                </Button>
              )}
            {canAdopt && (
              <Button
                disabled={busy}
                onClick={() => {
                  store(null)
                  command.current = null
                  setReconciled(false)
                  setMessage(
                    '已按当前实际状态结束本地核对；不代表原请求回执已确认，未重复执行或重建密钥。',
                  )
                }}
              >
                按当前状态结束本地核对
              </Button>
            )}
          </div>
          {!command.current && (
            <p className="page-note">
              敏感请求内容已不在内存，不能自动重发。请先读回执；不会猜口令或重建密钥。
            </p>
          )}
        </div>
      )}
      {status?.state === 'uninitialized' && !status.initialized && (
        <div className="vault-initialize">
          <h3>明确选择保护方式</h3>
          <div className="segmented" role="group" aria-label="密钥库初始化方式">
            <button
              type="button"
              disabled={blocked}
              aria-pressed={mode === 'keyring'}
              onClick={() => {
                setMode('keyring')
                setPassphrase('')
                setConfirmation('')
              }}
            >
              原生 Keyring（推荐）
            </button>
            <button
              type="button"
              disabled={blocked}
              aria-pressed={mode === 'passphrase'}
              onClick={() => {
                setMode('passphrase')
                setPassphrase('')
                setConfirmation('')
              }}
            >
              独立口令
            </button>
          </div>
          {mode === 'keyring' && !status.keyring_available && (
            <p className="page-note">
              系统目前报告 Keyring 不可用；显式初始化会重新检查，不会自动改用口令。
            </p>
          )}
        </div>
      )}
      {(needsInitPassphrase || needsUnlockPassphrase) && (
        <div className="vault-passphrase">
          <p className="page-note">
            使用本密钥库的独立口令，不会从账号登录口令复制。12–1024 UTF-8
            字节，内容只在本次表单内存。
          </p>
          <label>
            {needsInitPassphrase ? '设置独立口令' : '解锁独立口令'}
            <input
              type="password"
              autoComplete="off"
              disabled={blocked}
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
            />
          </label>
          {needsInitPassphrase && (
            <label>
              再次确认独立口令
              <input
                type="password"
                autoComplete="off"
                disabled={blocked}
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
              />
            </label>
          )}
        </div>
      )}
      <div className="button-row vault-actions">
        {status?.state === 'uninitialized' && !status.initialized && (
          <Button
            variant="primary"
            disabled={
              blocked ||
              (mode === 'passphrase' &&
                (!validVaultPassphrase(passphrase) || passphrase !== confirmation))
            }
            onClick={() => start('initialize')}
          >
            {mode === 'keyring' ? '初始化原生 Keyring' : '以独立口令初始化'}
          </Button>
        )}
        {status?.state === 'locked' && status.initialized && (
          <Button
            variant="primary"
            disabled={
              blocked || (status.backend === 'passphrase' && !validVaultPassphrase(passphrase))
            }
            onClick={() => start('unlock')}
          >
            {status.initialization_complete ? '明确解锁密钥库' : '解锁并核对现有初始化记录'}
          </Button>
        )}
        {status?.state === 'unlocked' && (
          <Button disabled={blocked} onClick={() => start('lock')}>
            明确锁定密钥库
          </Button>
        )}
        {status && status.legacy_credentials > 0 && (
          <Button
            disabled={blocked || status.state !== 'unlocked'}
            onClick={() => start('migrate-legacy')}
          >
            迁移 {status.legacy_credentials} 条旧式凭据
          </Button>
        )}
      </div>
      {status?.state === 'unlocked' && (
        <p className="page-note">锁定会阻止后续读取密钥；已经发送到模型或工具的请求无法撤回。</p>
      )}
      {status && status.legacy_credentials > 0 && (
        <p className="page-note">迁移需先解锁，原旧副本保留，不自动删除。</p>
      )}
      {status?.initialized && !status.initialization_complete && (
        <p className="page-note">
          已有初始化记录，不会重新建立 master。请读取回执或解锁核对原有记录。
        </p>
      )}
      {message && (
        <p className="vault-message" role="status">
          {message}
        </p>
      )}
    </section>
  )
}
