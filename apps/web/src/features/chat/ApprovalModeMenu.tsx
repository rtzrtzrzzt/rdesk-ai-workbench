import { useState } from 'react'
import { Check, ChevronDown, Hand, ShieldCheck, ShieldOff } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import { ComposerMenu } from './ComposerMenu'
import type { RunOptions } from '../../api/types'

export type ApprovalMode = NonNullable<RunOptions['approval_mode']>
export const approvalModes = [
  {
    value: 'manual',
    label: '每步批准',
    icon: Hand,
    description: '每次工具操作都先向你确认。',
  },
  {
    value: 'smart',
    label: '自动批准',
    icon: ShieldCheck,
    description: '由本地 AI 自动审核；无法确认的操作会被拒绝。',
  },
  {
    value: 'full_access',
    label: '完全开放权限',
    icon: ShieldOff,
    description: '无需审批，可访问工作区外的文件并运行主机命令。',
  },
] as const

export function ApprovalModeMenu({
  value,
  onChange,
  disabled = false,
}: {
  value: ApprovalMode
  onChange: (value: ApprovalMode) => void
  disabled?: boolean
}) {
  const [confirm, setConfirm] = useState(false)
  const selected = approvalModes.find((item) => item.value === value) ?? approvalModes[1]
  return (
    <>
      <ComposerMenu
        label="批准选项"
        className={`composer-permission-menu permission-${value}`}
        disabled={disabled}
        trigger={
          <>
            <selected.icon size={15} />
            <span>{value === 'full_access' ? '完全开放' : selected.label}</span>
            <ChevronDown size={12} />
          </>
        }
      >
        {(close) => (
          <>
            <div className="composer-menu-heading">批准选项</div>
            {approvalModes.map((item) => (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={item.value === value}
                key={item.value}
                disabled={disabled}
                onClick={() => {
                  close()
                  if (item.value === 'full_access' && value !== 'full_access') setConfirm(true)
                  else onChange(item.value)
                }}
              >
                <item.icon size={16} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
                {item.value === value && <Check size={15} />}
              </button>
            ))}
          </>
        )}
      </ComposerMenu>
      {confirm && (
        <Dialog title="启用完全开放权限？" onClose={() => setConfirm(false)}>
          <p>
            接下来发送的任务可以读写工作区外的文件、运行主机命令并访问网络，不再经过人工或 AI 审批。
          </p>
          <p className="page-note">
            操作以工作台服务账号的系统权限执行。账号归属与保险库接口保护仍然有效；主机命令不受文件工具的路径过滤约束。
          </p>
          <div className="dialog-actions">
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              取消
            </Button>
            <Button
              disabled={disabled}
              onClick={() => {
                onChange('full_access')
                setConfirm(false)
              }}
            >
              启用完全开放
            </Button>
          </div>
        </Dialog>
      )}
    </>
  )
}
