import type { DiagnosticsSnapshot } from '../../api/types'
import { Status } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'

const statusLabels: Record<string, string> = {
  observed: '已观测',
  partial: '部分数据',
  unknown: '未知',
  unavailable: '不可用',
  reference_only: '仅作参考',
  recent: '近期心跳',
  stale: '心跳过期',
}
const reasons: Record<string, string> = {
  ok: '正常',
  missing: '缺失',
  not_installed: '未安装',
  invalid_metadata: '元数据无效',
  query_unavailable: '查询不可用',
  scan_limit: '达到扫描上限',
  path_denied: '路径不可读',
  permission_denied: '权限不足',
  unsupported: '不支持',
  clock_skew: '时钟偏差',
  no_heartbeat: '未见心跳',
  stale: '已过期',
  reference_only: '仅作参考',
  database_scope: '数据库范围',
  other: '其他原因',
}
const categories: Record<string, string> = {
  artifacts: '产物',
  staging: '暂存',
  uploads: '上传',
  memory: '记忆',
  command_logs: '命令日志',
  logs: '运行日志',
  browser_profiles: '浏览器资料',
  backups: '备份',
  restore_staging: '恢复暂存',
  database: '数据库',
}
const bytes = (value: number | null | undefined) => (value == null ? '未知' : formatBytes(value))
const date = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : '未知'
const label = (value: string | undefined) => (value ? (statusLabels[value] ?? value) : '未知')
const reason = (value: string | undefined) => (value ? (reasons[value] ?? value) : '未知原因')
function ReadingStatus({ status, code }: { status?: string; code?: string }) {
  return (
    <span className="diagnostics-reading">
      <Status tone={status === 'partial' || status === 'unavailable' ? 'warning' : 'neutral'}>
        {label(status)}
      </Status>
      <small>{reason(code)}</small>
    </span>
  )
}

export function DiagnosticsSnapshotView({ data }: { data: DiagnosticsSnapshot }) {
  return (
    <div className="diagnostics-snapshot">
      <p className="page-note">
        快照时间：{date(data.generated_at)} · 诊断结构版本 {data.schema_version ?? '未知'}
      </p>
      <div className="diagnostics-overview">
        <article>
          <h3>应用</h3>
          <strong>{data.runtime.app_version ?? '未知'}</strong>
          <p>
            {data.runtime.platform} · {data.runtime.architecture}
          </p>
        </article>
        <article>
          <h3>数据卷可用空间</h3>
          <strong>{bytes(data.storage.data_volume_available_bytes)}</strong>
          <p>
            {label(data.storage.status)} · {reason(data.storage.reason_code)}
          </p>
        </article>
        <article>
          <h3>数据库</h3>
          <strong>PostgreSQL {data.database.postgres_version ?? '未知'}</strong>
          <p>
            {label(data.database.status)} · {reason(data.database.reason_code)}
          </p>
        </article>
      </div>
      <details>
        <summary>组件版本与数据库结构</summary>
        <dl className="run-meta">
          <dt>运行时</dt>
          <dd>
            {data.runtime.python_implementation} {data.runtime.python_version}
          </dd>
          <dt>数据库结构</dt>
          <dd>
            {label(data.database.schema_status)} ·{' '}
            {data.database.schema_revisions?.length
              ? data.database.schema_revisions.join(' · ')
              : '未获得结构修订号'}
          </dd>
          <dt>数据库大小</dt>
          <dd>{bytes(data.database.byte_size)} · PostgreSQL 只读查询</dd>
        </dl>
        <div className="diagnostics-table" tabIndex={0} role="region" aria-label="已安装组件版本">
          <table>
            <thead>
              <tr>
                <th scope="col">组件</th>
                <th scope="col">版本</th>
                <th scope="col">观测</th>
              </tr>
            </thead>
            <tbody>
              {data.runtime.dependencies.map((item) => (
                <tr key={item.name}>
                  <th scope="row">{item.name}</th>
                  <td>{item.version ?? '未知'}</td>
                  <td>
                    <ReadingStatus status={item.status} code={item.reason_code} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="page-note">来源：应用与已安装发行包元数据、PostgreSQL 只读查询。</p>
      </details>
      <details>
        <summary>两类任务队列与 Worker 心跳</summary>
        <div className="diagnostics-queues">
          {data.queues.map((queue) => (
            <article key={queue.queue}>
              <h3>{queue.queue === 'agent' ? 'Agent 队列' : '文件队列'}</h3>
              <ReadingStatus status={queue.status} code={queue.reason_code} />
              <dl className="run-meta">
                <dt>心跳状态</dt>
                <dd>{label(queue.heartbeat_status)}</dd>
                <dt>最新心跳</dt>
                <dd>{date(queue.latest_heartbeat_at)}</dd>
                <dt>心跳距今</dt>
                <dd>
                  {queue.heartbeat_age_seconds == null
                    ? '未知'
                    : `${queue.heartbeat_age_seconds.toFixed(1)} 秒`}
                </dd>
                <dt>近期 Worker 数</dt>
                <dd>{queue.recent_worker_count ?? '未知'}</dd>
                <dt>当前用户任务计数</dt>
                <dd>
                  {queue.counts && Object.keys(queue.counts).length
                    ? Object.entries(queue.counts).map(([state, count]) => (
                        <span key={state} className="diagnostics-count">
                          {state}: {count}
                        </span>
                      ))
                    : '未返回状态计数'}
                </dd>
              </dl>
            </article>
          ))}
        </div>
        <p className="page-note">
          来源：Worker 心跳记录与当前用户任务状态。心跳不代表单项工具或模型已经验真。
        </p>
      </details>
      <details>
        <summary>存储分类与观测范围</summary>
        <ReadingStatus status={data.storage.status} code={data.storage.reason_code} />
        <dl className="run-meta">
          <dt>总容量 / 空闲 / 当前可用</dt>
          <dd>
            {bytes(data.storage.data_volume_total_bytes)} /{' '}
            {bytes(data.storage.data_volume_free_bytes)} /{' '}
            {bytes(data.storage.data_volume_available_bytes)}
          </dd>
          <dt>扫描</dt>
          <dd>
            {data.storage.examined_entries} 个条目 · {data.storage.elapsed_ms.toFixed(1)} ms ·{' '}
            {data.storage.capacity_source}
          </dd>
        </dl>
        <div className="diagnostics-table" tabIndex={0} role="region" aria-label="存储分类观测结果">
          <table>
            <thead>
              <tr>
                <th scope="col">固定分类</th>
                <th scope="col">表观大小</th>
                <th scope="col">文件数 / 跳过条目</th>
                <th scope="col">状态与原因</th>
                <th scope="col">来源</th>
              </tr>
            </thead>
            <tbody>
              {data.storage.categories.map((item) => (
                <tr key={item.category}>
                  <th scope="row">{categories[item.category]}</th>
                  <td>
                    {bytes(item.apparent_bytes)}
                    {item.status === 'partial' ? '（部分）' : ''}
                  </td>
                  <td>
                    {item.file_count ?? '未知'} / {item.skipped_entries ?? '未知'}
                  </td>
                  <td>
                    <ReadingStatus status={item.status} code={item.reason_code} />
                  </td>
                  <td>
                    {item.source === 'pg_database_size'
                      ? 'pg_database_size'
                      : item.source === 'nofollow_stat_only'
                        ? '不跟随链接的文件元数据'
                        : '未知'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="page-note">
          实时、非原子观测；表观大小不等于磁盘实际占用，分类可能有重叠，不应相加作为总量。不读取文件正文，不纳入外部工作区文件；部分数据保留缺失原因。
        </p>
      </details>
      <details>
        <summary>近期受控错误码</summary>
        <ReadingStatus status={data.recent_errors.status} code={data.recent_errors.reason_code} />
        <p className="page-note">
          按记录创建时间回看 {data.recent_errors.lookback_hours ?? '未知'} 小时，每个来源最多采样{' '}
          {data.recent_errors.maximum_rows_per_source ?? '未知'} 条；实际采样{' '}
          {data.recent_errors.sampled_rows} 条。
          {data.recent_errors.sample_limit_reached
            ? '已触及采样上限，计数并不完整。'
            : '未触及采样上限。'}
          仅统计受控错误码，不读取原始日志。
        </p>
        {data.recent_errors.counts.length ? (
          <div
            className="diagnostics-table"
            tabIndex={0}
            role="region"
            aria-label="近期受控错误码计数"
          >
            <table>
              <thead>
                <tr>
                  <th scope="col">来源</th>
                  <th scope="col">错误码</th>
                  <th scope="col">采样内条数</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_errors.counts.map((item) => (
                  <tr key={`${item.source}:${item.code}`}>
                    <th scope="row">{item.source === 'run_error' ? '任务' : '模型尝试'}</th>
                    <td>
                      <code>{item.code}</code>
                    </td>
                    <td>{item.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="page-note">
            {data.recent_errors.status === 'observed'
              ? '采样记录中未见受控错误码。'
              : '没有可展示的受控错误码；请结合观测状态与原因。'}
          </p>
        )}
      </details>
      <details>
        <summary>模型参考信息 · PLE 未测量</summary>
        <dl className="run-meta">
          <dt>性质</dt>
          <dd>仅作参考（reference_only），不表示能力或内存验真</dd>
          <dt>连接数</dt>
          <dd>{data.models.connection_count ?? '未知'}</dd>
          <dt>受管部署清单</dt>
          <dd>{data.models.managed_manifest_present ? '存在' : '未发现'}</dd>
          <dt>引擎</dt>
          <dd>
            {data.models.engine ?? '未知'} · {data.models.engine_version ?? '未知版本'}
          </dd>
          <dt>此前核验的权重大小</dt>
          <dd>{bytes(data.models.previously_verified_asset_bytes)}</dd>
          <dt>PLE 使用量</dt>
          <dd>{data.models.ple_usage === 'not_measured' ? '未测量（not_measured）' : '未提供'}</dd>
        </dl>
        <p className="page-note">
          诊断不扫描或打包权重；参考清单字节数不代表本次加载量或 PLE 实际用量。
        </p>
      </details>
    </div>
  )
}
