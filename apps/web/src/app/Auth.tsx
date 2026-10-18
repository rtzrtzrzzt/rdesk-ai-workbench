import { ArrowRight, ArrowLeft, Monitor, Sparkles } from 'lucide-react'
import { Button, Status } from '../components/ui'

export function AuthView({
  busy = false,
  error,
  onSubmit,
  onBack,
}: {
  busy?: boolean
  error?: string
  onSubmit?: () => void
  onBack?: () => void
}) {
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
        <h1>继续你的本机工作。</h1>
        <p>工作台仅允许本机访问，无需登录密码。</p>
        <form
          className="auth-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (onSubmit && !busy) onSubmit()
          }}
        >
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={!onSubmit || busy}>
            {busy ? '正在连接…' : '进入本机工作台'}
            <ArrowRight size={16} />
          </Button>
          {!onSubmit && <Status>本机服务尚未连接</Status>}
        </form>
        <p className="auth-footer">
          <Monitor size={13} />
          凭据保险库仍需独立解锁
        </p>
      </div>
    </main>
  )
}
