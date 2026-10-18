import { useQuery } from '@tanstack/react-query'
import { accountApi } from '../../api/account'
import { Button } from '../../components/ui'
import { useSettingsAttention } from './SettingsGroup'
import './account.css'

export function AccountSecurity({ userId }: { userId: string }) {
  const security = useQuery({
    queryKey: ['account-security', userId],
    queryFn: accountApi.security,
    refetchInterval: 30000,
  })
  useSettingsAttention('account-error', security.isError, '本机会话信息需要处理')
  return (
    <section className="settings-account">
      <h2>本机会话</h2>
      <p className="page-note">
        工作台仅允许本机访问，无需登录密码。凭据保险库的独立口令或系统 Keyring 仍然保留。
        实际点击或按键会续期；后台任务、事件流和状态轮询不会延长空闲期限。
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
      <Button onClick={() => void security.refetch()}>刷新会话状态</Button>
    </section>
  )
}
