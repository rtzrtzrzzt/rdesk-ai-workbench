import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { SlidersHorizontal } from 'lucide-react'
import { IconButton } from '../../components/ui'
import { Dialog } from '../../components/Dialog'
import { settingsApi } from '../../api/settings'
import type { RunOptions, ModelConnection } from '../../api/types'
import { EffectiveDefaults, RunOptionsFields, validRunOptions } from '../settings/runtimeOptions'
import '../settings/runtime.css'
export { RunOptionsFields, validRunOptions } from '../settings/runtimeOptions'
export function TaskOptions({
  value,
  onChange,
  disabled,
  workspaceId,
  models = [],
}: {
  value: RunOptions | null
  onChange: (value: RunOptions | null) => void
  disabled: boolean
  workspaceId?: string
  models?: ModelConnection[]
}) {
  const [open, setOpen] = useState(false)
  const defaults = useQuery({
    queryKey: ['workspace-runtime-defaults', workspaceId],
    queryFn: () => settingsApi.workspaceRuntime(workspaceId ?? ''),
    enabled: !!workspaceId,
  })
  const user = useQuery({
    queryKey: ['runtime-defaults'],
    queryFn: settingsApi.runtime,
    enabled: !!workspaceId,
  })
  return (
    <>
      <IconButton
        icon={SlidersHorizontal}
        label={value === null ? '本次任务设置' : '本次任务设置 · 已自定义'}
        disabled={disabled}
        onClick={() => setOpen(true)}
      />
      {open && (
        <Dialog title="本次任务设置" onClose={() => setOpen(false)}>
          <div className="task-options">
            <p className="page-note">
              按系统 → 用户 → 工作区 → 本次任务继承。只在创建新任务时冻结，不改变执行中任务。子
              Agent 最多 3 个；模型生成槽独立
              {user.data?.model_generation_capacity === 1 ? '，当前为 1 个' : ''}。
            </p>
            <label className="task-option-mode">
              配置来源
              <select
                aria-label="配置来源"
                value={value === null ? 'inherit' : 'override'}
                disabled={disabled}
                onChange={(event) => {
                  if (event.target.value === 'inherit') onChange(null)
                  else if (defaults.data) onChange({ ...defaults.data.effective.options })
                }}
              >
                <option value="inherit">继承当前工作区有效默认（提交时服务端冻结）</option>
                <option value="override" disabled={!defaults.data || defaults.isError}>
                  覆盖本次任务
                </option>
              </select>
            </label>
            {value !== null && (
              <>
                <RunOptionsFields value={value} onChange={onChange} disabled={disabled} />
                {!validRunOptions(value) && (
                  <p role="alert" className="form-error">
                    请填写范围内的整数、0.5–0.95 的上下文比例，并明确选择执行配置。
                  </p>
                )}
              </>
            )}
            {defaults.data && (
              <EffectiveDefaults effective={defaults.data.effective} models={models} />
            )}
            {defaults.error && (
              <p role="alert" className="form-error">
                {defaults.error.message}
              </p>
            )}
            {!defaults.data && !defaults.error && (
              <p className="page-note">
                {workspaceId ? '正在读取当前工作区的有效配置…' : '选择工作区后可读取有效默认。'}
              </p>
            )}
            <p className="page-note">
              发送框默认继承用户与工作区模型；明确选择其它模型时，仅覆盖新建任务，执行中任务保持原连接。
            </p>
          </div>
        </Dialog>
      )}
    </>
  )
}
