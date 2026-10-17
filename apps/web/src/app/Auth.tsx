import { useState } from 'react'
import { ArrowRight, ArrowLeft, LockKeyhole, Sparkles } from 'lucide-react'
import { Button, Status } from '../components/ui'

export function AuthView({
  mode,
  busy = false,
  error,
  onSubmit,
  onBack,
}: {
  mode: 'initialize' | 'login'
  busy?: boolean
  error?: string
  onSubmit?: (username: string, password: string) => void
  onBack?: () => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const initialize = mode === 'initialize'
  return (
    <main className="auth-page">
      {onBack && (
        <Button variant="ghost" className="auth-back" onClick={onBack}>
          <ArrowLeft size={16} />
          返回工作台
        </Button>
      )}
      <div className="auth-content">
        <div className="brand-mark">
          <Sparkles size={25} strokeWidth={1.45} />
        </div>
        <span className="auth-brand">AI 工作台</span>
        <h1>{initialize ? '开始，专注于你的工作。' : '欢迎回来。'}</h1>
        <p>
          {initialize
            ? '建立所有者账号，管理你的模型、文件与任务。'
            : '登录你的工作台，继续尚未完成的工作。'}
        </p>
        <form
          className="auth-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (onSubmit && !busy) onSubmit(username.trim(), password)
          }}
        >
          <label htmlFor="username">{initialize ? '所有者用户名' : '用户名'}</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="输入用户名"
          />
          <label htmlFor="password">密码</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete={initialize ? 'new-password' : 'current-password'}
            required
            minLength={initialize ? 12 : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={initialize ? '至少 12 个字符' : '输入密码'}
          />
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            disabled={!onSubmit || busy || !username.trim() || !password}
          >
            {busy ? '正在连接…' : initialize ? '创建并进入工作台' : '登录'}
            <ArrowRight size={16} />
          </Button>
          {!onSubmit && <Status>身份服务尚未连接</Status>}
        </form>
        <p className="auth-footer">
          <LockKeyhole size={13} />
          凭据仅发送至当前工作台服务
        </p>
      </div>
    </main>
  )
}
