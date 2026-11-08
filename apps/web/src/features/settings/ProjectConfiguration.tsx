import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RefreshCw } from 'lucide-react'
import { projectConfigurationApi } from '../../api/projectConfiguration'
import { Button, Status } from '../../components/ui'
import { useSettingsAttention } from './SettingsGroup'
import './projectConfiguration.css'

const labels = { missing: '未配置', valid: '有效', invalid: '无效', unavailable: '无法读取' }

export function ProjectConfiguration({
  workspaceId,
  active,
}: {
  workspaceId: string
  active: boolean
}) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['project-configuration', workspaceId],
    queryFn: () => projectConfigurationApi.project(workspaceId),
    enabled: active,
    staleTime: 10_000,
  })
  const [notice, setNotice] = useState('')
  const downloadUrl = useRef<string | null>(null)
  async function refresh() {
    await query.refetch()
    await client.invalidateQueries({ queryKey: ['workspace-runtime-defaults', workspaceId] })
  }
  useEffect(
    () => () => {
      if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current)
    },
    [],
  )
  useSettingsAttention(
    'project-configuration',
    query.isError || query.data?.status === 'invalid' || query.data?.status === 'unavailable',
    '项目配置需要检查',
  )
  function downloadTemplate() {
    if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current)
    downloadUrl.current = URL.createObjectURL(
      new Blob([JSON.stringify({ version: 1, commands: {}, defaults: {} }, null, 2) + '\n'], {
        type: 'application/json',
      }),
    )
    const link = document.createElement('a')
    link.href = downloadUrl.current
    link.download = 'rdesk-config.template.json'
    document.body.append(link)
    try {
      link.click()
    } finally {
      link.remove()
    }
    setNotice(
      '空白模板已交给浏览器下载。请自行编辑，再保存为项目的 .rdesk/config.json；已有配置不会被覆盖。',
    )
  }
  return (
    <section className="section-card project-configuration">
      <div className="section-title">
        <h3>项目配置 · .rdesk</h3>
        {query.data && (
          <Status
            tone={
              query.data.status === 'invalid' || query.data.status === 'unavailable'
                ? 'warning'
                : 'neutral'
            }
          >
            {labels[query.data.status]}
          </Status>
        )}
      </div>
      <p className="page-note">
        在项目根目录使用 .rdesk/config.json（v1 JSON，最多 64
        KiB），随项目携带解释器、测试命令与运行偏好。解释器和命令使用 argv
        数组，只作为任务上下文声明，不会自动执行或修改全局解释器。
      </p>
      <div className="button-row">
        <Button disabled={query.isFetching} onClick={() => void refresh()}>
          <RefreshCw size={14} /> 刷新配置
        </Button>
        <Button onClick={downloadTemplate}>
          <Download size={14} /> 下载空白模板
        </Button>
      </div>
      {query.error && (
        <p role="alert" className="form-error">
          {query.error.message}
        </p>
      )}
      {query.data && (
        <>
          <code className="project-configuration-path">{query.data.path}</code>
          <p className="page-note" role="status">
            {query.data.message}
          </p>
          {query.data.configuration && (
            <details>
              <summary>查看已校验的项目配置</summary>
              <pre>{JSON.stringify(query.data.configuration, null, 2)}</pre>
            </details>
          )}
        </>
      )}
      {!query.data && query.isFetching && <p role="status">正在读取项目配置…</p>}
      {notice && (
        <p role="status" className="page-note">
          {notice}
        </p>
      )}
      <p className="page-note">
        优先级：系统 → 账户 → 项目配置 → 下方工作区覆盖 →
        单次任务。仅新任务读取配置。无效配置整体不应用，继续继承已有设置。
      </p>
      <details>
        <summary>支持的字段与边界</summary>
        <p className="page-note">
          version 固定为 1；interpreter 为可执行文件及参数数组；commands 为命令名称到 argv
          数组的映射。defaults 支持
          command_timeout_seconds、checkpoint_rounds、model_retry_limit、retry_backoff_seconds、context_target_ratio。
        </p>
        <p className="page-note">
          规则继续使用根目录 AGENTS.md；插件与 Skill
          使用现有标准格式并通过插件入口安装。项目配置不支持模型连接、审批、沙箱、系统或网络权限，也不保存凭据、provider
          或 auth 数据。下载的空白模板不含凭据；请勿将秘密写入命令参数。
        </p>
      </details>
    </section>
  )
}
