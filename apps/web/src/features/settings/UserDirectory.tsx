import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Copy, RefreshCw } from 'lucide-react'
import { projectConfigurationApi } from '../../api/projectConfiguration'
import { Button } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'
import { useSettingsAttention } from './SettingsGroup'
import './projectConfiguration.css'

const categories: Record<string, string> = {
  artifacts: '任务产物',
  staging: '任务暂存',
  uploads: '上传文件',
  memory: '记忆文件',
  command_logs: '命令日志',
  logs: '服务日志',
  browser_profiles: '浏览器资料',
  backups: '维护备份',
  restore_staging: '恢复暂存',
  database: 'PostgreSQL 数据库（整库）',
}
const reasons: Record<string, string> = {
  ok: '已观测',
  missing: '尚无目录',
  scan_limit: '达到扫描上限，仅为部分数据',
  path_denied: '路径不可读或含链接',
  permission_denied: '读取权限不足',
  unsupported: '当前平台不支持安全扫描',
  query_unavailable: '数据库查询不可用',
  database_scope: '数据库整体大小',
}

export function UserDirectory({ active }: { active: boolean }) {
  const query = useQuery({
    queryKey: ['user-data-directory'],
    queryFn: projectConfigurationApi.directory,
    enabled: active,
    staleTime: 30_000,
  })
  const [notice, setNotice] = useState('')
  useSettingsAttention('user-directory', query.isError, '用户目录读取未完成')
  async function copyPath() {
    if (!query.data) return
    try {
      await navigator.clipboard.writeText(query.data.root_path)
      setNotice('已复制服务端用户目录路径。')
    } catch {
      setNotice('浏览器未允许复制。请选中下方路径手动复制。')
    }
  }
  return (
    <section className="section-card project-configuration">
      <div className="section-title">
        <h3>用户目录</h3>
        <Button disabled={query.isFetching} onClick={() => void query.refetch()}>
          <RefreshCw size={14} /> 刷新占用
        </Button>
      </div>
      <p className="page-note">
        这是运行 RDesk 服务的机器上的实际目录。账户规则、扩展及任务文件沿用现有存储位置。
      </p>
      {query.error && (
        <p role="alert" className="form-error">
          {query.error.message}
        </p>
      )}
      {!query.data && query.isFetching && <p role="status">正在读取用户目录…</p>}
      {query.data && (
        <>
          <label>
            <span>
              实际目录 ·{' '}
              {query.data.source === 'environment_override'
                ? 'WORKBENCH_DATA_DIR 覆盖'
                : '系统标准位置'}
            </span>
            <input aria-label="服务端用户目录" readOnly value={query.data.root_path} />
          </label>
          <Button onClick={() => void copyPath()}>
            <Copy size={14} /> 复制目录路径
          </Button>
          <p className="page-note">
            请在运行 RDesk
            的电脑上，将此路径粘贴到文件管理器。远程访问时，这不是浏览器所在电脑的路径。
          </p>
          <p className="page-note" role="status">
            {notice}
          </p>
          <dl className="run-meta">
            <dt>全局规则</dt>
            <dd>
              <code>{query.data.global_rules_path}</code>
            </dd>
            <dt>扩展存储</dt>
            <dd>
              <code>{query.data.extensions_path}</code>
            </dd>
            <dt>会话与配置记录</dt>
            <dd>PostgreSQL；复制用户目录不能完整备份或迁移会话。</dd>
          </dl>
          <div
            className="project-storage-table"
            tabIndex={0}
            role="region"
            aria-label="用户目录分类占用"
          >
            <table>
              <thead>
                <tr>
                  <th scope="col">分类</th>
                  <th scope="col">逻辑大小</th>
                  <th scope="col">观测</th>
                </tr>
              </thead>
              <tbody>
                {query.data.storage.categories.map((item) => (
                  <tr key={item.category}>
                    <th scope="row">{categories[item.category] ?? item.category}</th>
                    <td>
                      {item.apparent_bytes == null ? '未知' : formatBytes(item.apparent_bytes)}
                    </td>
                    <td>
                      {reasons[item.reason_code] ??
                        (item.status === 'partial' ? '部分数据' : '未知')}
                      {item.skipped_entries ? ` · 跳过 ${item.skipped_entries} 项` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="page-note">
            这是有上限的实时分类观测，并非目录总量；不含项目原文件、模型权重、凭据或扩展包占用。PostgreSQL
            大小单独计算，可能位于其它磁盘。
          </p>
          <p className="page-note">
            保留与清理：任务文件通过任务管理，日志、暂存与备份通过下方维护功能按支持范围处理。全局规则、扩展与凭据继续由各自入口管理；本页不会自动删除、迁移或修改文件。
          </p>
        </>
      )}
    </section>
  )
}
