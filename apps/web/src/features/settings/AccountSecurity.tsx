import { useSettingsAttention } from './SettingsGroup'
import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { accountApi } from '../../api/account'
import type { PasswordChange, PasswordChangeReceipt } from '../../api/types'
import { Button } from '../../components/ui'
import './account.css'

export function validNewPassword(value: string) {
  const count = Array.from(value).length
  return count >= 12 && count <= 1024
}
export function AccountSecurity({ userId }: { userId: string }) {
  const id = useId()
  const security = useQuery({
    queryKey: ['account-security', userId],
    queryFn: accountApi.security,
    refetchInterval: 30000,
  })
  const [current, setCurrent] = useState(''),
    [password, setPassword] = useState(''),
    [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false),
    [unknown, setUnknown] = useState(false)
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState<PasswordChangeReceipt | null>(null)
  const pending = useRef<PasswordChange | null>(null)
  const [passwordOpen, setPasswordOpen] = useState(false)
  useEffect(() => {
    if (unknown || error) setPasswordOpen(true)
  }, [unknown, error])
  useSettingsAttention('account', unknown, '密码修改结果待核对')
  useSettingsAttention('account-error', !!error || security.isError, '账号安全信息需要处理')
  async function submit() {
    if (busy) return
    if (!pending.current) {
      if (
        !current ||
        Array.from(current).length > 1024 ||
        !validNewPassword(password) ||
        password !== confirm
      ) {
        setError('请填写当前密码，并确认一致的 12–1024 字符新密码。')
        return
      }
      pending.current = {
        current_password: current,
        new_password: password,
        request_id: crypto.randomUUID(),
      }
    }
    setBusy(true)
    setError('')
    try {
      const result = await accountApi.changePassword(pending.current)
      setReceipt(result)
      pending.current = null
      setCurrent('')
      setPassword('')
      setConfirm('')
      setUnknown(false)
      await security.refetch()
    } catch (error) {
      const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      setUnknown(uncertain)
      if (!uncertain) pending.current = null
      if (error instanceof ApiError && error.status === 401) {
        setCurrent('')
        setPassword('')
        setConfirm('')
      }
      setError(
        `${error instanceof Error ? error.message : '密码修改未完成。'}${uncertain ? ' 原密码提交仅保留在本页内存，核对复用同一内容与请求标识。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="settings-account">
      <h2>账号安全</h2>
      <p className="page-note">
        仅实际点击或按键会节流续期；后台任务、事件流和状态轮询不会延长空闲期限。
      </p>
      {security.data && (
        <dl className="run-meta">
          <dt>空闲超时</dt>
          <dd>{security.data.idle_timeout_seconds} 秒</dd>
          <dt>服务器记录的最近活动</dt>
          <dd>{new Date(security.data.last_active_at).toLocaleString()}</dd>
          <dt>会话绝对到期</dt>
          <dd>{new Date(security.data.session_expires_at).toLocaleString()}</dd>
        </dl>
      )}
      {security.error && (
        <p role="alert" className="form-error">
          {security.error.message}
        </p>
      )}
      <Button onClick={() => void security.refetch()}>只读刷新会话状态</Button>
      <details open={passwordOpen} onToggle={(event) => setPasswordOpen(event.currentTarget.open)}>
        <summary>修改登录密码{unknown ? ' · 结果待核对' : ''}</summary>
        <p className="page-note">
          修改成功后撤销其他登录会话，保留当前会话。密码仅发送到当前工作台，不写入浏览器存储。
        </p>
        <form
          className="stacked-form"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <fieldset disabled={busy || unknown} className="account-password-fields">
            <label htmlFor={`${id}-current`}>
              当前密码
              <input
                id={`${id}-current`}
                name="current_password"
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </label>
            <label htmlFor={`${id}-new`}>
              新密码 · 12–1024 字符
              <input
                id={`${id}-new`}
                name="new_password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            <label htmlFor={`${id}-confirm`}>
              再次确认新密码
              <input
                id={`${id}-confirm`}
                name="new_password_confirmation"
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
            </label>
          </fieldset>
          {unknown && (
            <p className="memory-conflict">
              结果未确认。请保留本页后核对原请求：{pending.current?.request_id}
              ；不要改成另一组密码重发。
            </p>
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <Button
            variant="primary"
            type="submit"
            disabled={
              busy ||
              (!unknown && (!current || !validNewPassword(password) || password !== confirm))
            }
          >
            {busy ? '正在提交…' : unknown ? '核对本次密码修改' : '修改密码并撤销其他会话'}
          </Button>
        </form>
      </details>
      {receipt && (
        <div role="status" className="account-password-receipt">
          <strong>服务器已确认密码修改</strong>
          <p>修改时间：{new Date(receipt.changed_at).toLocaleString()}</p>
          <p>已撤销其他会话：{receipt.other_sessions_revoked}；当前会话保留。</p>
          <small>回执：{receipt.request_id}</small>
        </div>
      )}
    </section>
  )
}
