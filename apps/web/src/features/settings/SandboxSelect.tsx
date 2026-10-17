import { useQuery } from '@tanstack/react-query'
import { settingsApi } from '../../api/settings'
import type { RunOptions, SandboxCapability } from '../../api/types'
import { Button } from '../../components/ui'
import './runtime.css'

export type SandboxProfile = NonNullable<RunOptions['sandbox_profile']>
export const sandboxLabels: Record<SandboxProfile, string> = {
  native_scoped: '主机原生（不隔离文件/网络）',
  linux_bwrap: '系统沙箱（禁用网络）',
}
export function sandboxLabel(value: RunOptions['sandbox_profile'] | null) {
  return value ? sandboxLabels[value] : '未提供执行配置'
}
function available(capability: SandboxCapability | undefined) {
  return (
    capability?.status === 'ready' &&
    (capability.provider === 'native_scoped' || capability.probe_performed === true)
  )
}
const flag = (value: boolean | undefined) =>
  value === true ? '是' : value === false ? '否' : '未知'

export function SandboxSelect({
  value,
  onChange,
  disabled,
  inherit = false,
}: {
  value: RunOptions['sandbox_profile'] | null
  onChange: (value: SandboxProfile | null) => void
  disabled: boolean
  inherit?: boolean
}) {
  const query = useQuery({ queryKey: ['sandbox-capabilities'], queryFn: settingsApi.sandbox })
  return (
    <div className="sandbox-select">
      <label>
        执行配置
        <select
          value={value ?? ''}
          disabled={disabled}
          onChange={(event) => {
            const selected = event.target.value
            if (selected === '' && inherit) onChange(null)
            else if (selected === 'native_scoped' || selected === 'linux_bwrap') {
              if (
                !query.isError &&
                available(query.data?.find((item) => item.provider === selected))
              )
                onChange(selected)
            }
          }}
        >
          <option value="" disabled={!inherit}>
            {inherit ? '继承用户 / 系统执行配置' : '请选择执行配置'}
          </option>
          {(['native_scoped', 'linux_bwrap'] as const).map((profile) => {
            const capability = query.data?.find((item) => item.provider === profile)
            return (
              <option
                key={profile}
                value={profile}
                disabled={query.isError || !available(capability)}
              >
                {sandboxLabels[profile]}
                {!available(capability) ? ' · 当前不可选' : ''}
              </option>
            )
          })}
        </select>
      </label>
      <small>创建新任务时冻结；不可用时不会自动改用其他执行配置。</small>
      {query.data?.map((capability) => (
        <details key={capability.provider} className="sandbox-capability">
          <summary>
            {sandboxLabels[capability.provider]} ·{' '}
            {capability.status === 'ready'
              ? available(capability)
                ? '可选'
                : '缺少探测证据'
              : capability.status === 'unsupported'
                ? '不支持'
                : '不可用'}
            {!available(capability) && ` · ${capability.reason_code ?? capability.reason}`}
          </summary>
          <p>{capability.reason}</p>
          <dl className="run-meta">
            <dt>探测</dt>
            <dd>
              {capability.probe_performed === true ? '已执行' : '未执行'} ·{' '}
              {capability.reason_code ?? '无原因码'}
            </dd>
            <dt>文件 / 网络 / 进程隔离</dt>
            <dd>
              {flag(capability.filesystem_isolation)} / {flag(capability.network_isolation)} /{' '}
              {flag(capability.process_isolation)}
            </dd>
            <dt>CPU / 内存 / 工作区磁盘硬上限</dt>
            <dd>
              {flag(capability.hard_cpu_limit)} / {flag(capability.hard_memory_limit)} /{' '}
              {flag(capability.hard_workspace_disk_limit)}
            </dd>
            <dt>平台与版本</dt>
            <dd>
              {capability.platform} · {capability.architecture} · {capability.version ?? '未知'}
            </dd>
            <dt>读取时间</dt>
            <dd>
              {capability.checked_at ? new Date(capability.checked_at).toLocaleString() : '未知'}
            </dd>
          </dl>
        </details>
      ))}
      {!query.data && !query.error && <small>正在读取实际执行能力…</small>}
      {query.error && (
        <p role="alert" className="form-error">
          {query.error.message}
        </p>
      )}
      {query.data?.length === 0 && <small>服务端没有返回执行配置能力。</small>}
      <Button disabled={disabled || query.isFetching} onClick={() => void query.refetch()}>
        刷新执行能力
      </Button>
    </div>
  )
}
