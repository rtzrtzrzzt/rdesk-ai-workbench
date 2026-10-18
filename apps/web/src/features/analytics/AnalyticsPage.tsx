import { lazy, Suspense, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { BarChart3, RefreshCw } from 'lucide-react'
import { analyticsApi } from '../../api/analytics'
import type { AnalyticsQueryFilters } from '../../api/analytics'
import { ApiError } from '../../api/client'
import type {
  AnalyticsSummary,
  ModelConnection,
  TokenMetric,
  UsageCall,
  UsageMetrics,
  Workspace,
} from '../../api/types'
import { Button, EmptyState, PageHeading } from '../../components/ui'
import { HardwarePanel } from './HardwarePanel'
import { CallsTable } from './CallsTable'
import {
  AnalyticsSection,
  MetricCard,
  callStatusLabels,
  countLabel,
  coverageLabel,
  durationLabel,
  numberLabel,
  percentLabel,
  utcLabel,
} from './ui'

const AnalyticsTrend = lazy(() => import('./AnalyticsTrend'))
function ReadError({ error, retry }: { error: unknown; retry: () => void }) {
  const unavailable = error instanceof ApiError && (error.status === 404 || error.status === 503)
  return (
    <div className="analytics-error" role="alert">
      <p>
        {unavailable
          ? '数据看板服务尚未就绪，指标未读取。'
          : error instanceof Error
            ? error.message
            : '数据暂时无法读取。'}
      </p>
      <Button onClick={retry}>
        <RefreshCw size={14} />
        重新读取
      </Button>
    </div>
  )
}
function noSampleLabel(value: number | null, samples: number) {
  return value === null ? (samples === 0 ? '无可计算样本' : '未知') : numberLabel(value)
}
function OverviewMetrics({
  metrics,
  cost,
}: {
  metrics: UsageMetrics
  cost: AnalyticsSummary['cost']
}) {
  const cache = metrics.cache
  const latency = metrics.first_visible_ms
  const output = metrics.request_output_rate
  const cacheValue =
    cache.token_hit_ratio === null
      ? metrics.attempts === 0
        ? '无调用样本'
        : cache.known_attempts === 0
          ? '未知'
          : '无非零输入样本'
      : percentLabel(cache.token_hit_ratio)
  return (
    <>
      <div className="analytics-metric-grid">
        <MetricCard
          title="模型调用"
          value={countLabel(metrics.attempts)}
          unit="次"
          hint="一次模型 attempt 计为一次调用，包含已知失败尝试。"
        >
          <div className="analytics-status-counts">
            {metrics.by_status.map((item) => (
              <span key={item.status}>
                {callStatusLabels[item.status] ?? item.status} {countLabel(item.count)}
              </span>
            ))}
          </div>
        </MetricCard>
        <MetricCard
          title="已知用量覆盖"
          value={
            metrics.attempts === 0 ? '无调用样本' : percentLabel(metrics.known_usage_coverage_ratio)
          }
          hint={coverageLabel(metrics.known_usage_attempts, metrics.attempts)}
        >
          <p className="analytics-method">
            用量记录：{countLabel(metrics.usage_records)}。无用量记录的调用仍计入分母。
          </p>
        </MetricCard>
        <MetricCard
          title="缓存读取 token 命中率"
          value={cacheValue}
          hint={
            <>
              可观测覆盖 {percentLabel(cache.coverage_ratio)} ·{' '}
              {coverageLabel(cache.known_attempts, cache.eligible_attempts)}
            </>
          }
        >
          <p className="analytics-method">
            仅在输入总量与缓存读取量共同已知、口径一致的调用中，按读取 token 总和 / 输入 token
            总和计算。
          </p>
          <details className="analytics-source">
            <summary>查看缓存分母</summary>
            <dl>
              <dt>共同观测输入 tokens</dt>
              <dd>{countLabel(cache.observed_input_tokens)}</dd>
              <dt>共同观测读取 tokens</dt>
              <dd>{countLabel(cache.observed_read_tokens)}</dd>
              <dt>请求命中率</dt>
              <dd>{percentLabel(cache.request_hit_ratio)}</dd>
              <dt>已知输入 token 范围覆盖</dt>
              <dd>
                {percentLabel(cache.known_input_token_coverage_ratio)}，不是全部真实输入覆盖率
              </dd>
              <dt>口径不一致的尝试</dt>
              <dd>{countLabel(cache.inconsistent_attempts)}</dd>
            </dl>
          </details>
        </MetricCard>
        <MetricCard
          title="首段可见文本延迟"
          value={
            latency.p50 === null
              ? noSampleLabel(latency.p50, latency.sample_count)
              : durationLabel(latency.p50)
          }
          hint={
            <>
              P50 · P95{' '}
              {latency.p95 === null
                ? latency.sample_count === 0
                  ? '无可计算样本'
                  : '未知'
                : durationLabel(latency.p95)}{' '}
              · {countLabel(latency.sample_count)} 个样本
            </>
          }
        >
          <p className="analytics-method">
            从请求发出至首段用户可见文本。覆盖 {percentLabel(latency.coverage_ratio)}
            ，不以工具或推理事件代替。
          </p>
        </MetricCard>
        <MetricCard
          title="端到端输出速率"
          value={noSampleLabel(output.weighted_tokens_per_second, output.sample_count)}
          unit={output.weighted_tokens_per_second === null ? undefined : 'tok/s'}
          hint={
            <>
              P50 {numberLabel(output.p50)} · P95 {numberLabel(output.p95)} ·{' '}
              {countLabel(output.sample_count)} 个样本
            </>
          }
        >
          <p className="analytics-method">
            同一可算子集的输出 tokens 总和 /
            请求持续时间总和，包含等待、输入处理与生成时间；不是纯解码速度。覆盖{' '}
            {percentLabel(output.coverage_ratio)}。
          </p>
        </MetricCard>
        <MetricCard title="API 费用" value="未知" hint={cost.reason}>
          <p className="analytics-method">
            费用尚不可计算；本地运行的硬件、电费与折旧也未纳入计量。
          </p>
        </MetricCard>
      </div>
      <AnalyticsSection
        title="已知 token 用量"
        description="各项仅汇总实际已知值，未知保留为未知；缓存读取、写入与推理可能属于输入/输出的子集，不将这些列再次相加。"
      >
        <div className="analytics-token-grid">
          {(
            [
              { name: '输入 tokens', metric: metrics.input_tokens },
              { name: '输出 tokens', metric: metrics.output_tokens },
              { name: '缓存读取 tokens', metric: metrics.cache_read_tokens },
              { name: '缓存写入 tokens', metric: metrics.cache_write_tokens },
              { name: '推理 tokens', metric: metrics.reasoning_tokens },
            ] satisfies { name: string; metric: TokenMetric }[]
          ).map((item) => (
            <div key={item.name}>
              <h3>{item.name}</h3>
              <strong>{countLabel(item.metric.known_tokens)}</strong>
              <p>{coverageLabel(item.metric.known_attempts, item.metric.eligible_attempts)}</p>
              <p>覆盖 {percentLabel(item.metric.coverage_ratio)}</p>
            </div>
          ))}
        </div>
      </AnalyticsSection>
    </>
  )
}
export function AnalyticsPage({
  authenticated,
  active,
  workspaces,
  models,
  onOpenRun,
}: {
  authenticated: boolean
  active: boolean
  workspaces: Workspace[]
  models: ModelConnection[]
  onOpenRun: (call: UsageCall) => Promise<void>
}) {
  const [window, setWindow] = useState<AnalyticsQueryFilters['window']>('24h')
  const [workspaceId, setWorkspaceId] = useState('')
  const [modelId, setModelId] = useState('')
  const filters: AnalyticsQueryFilters = {
    window,
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    ...(modelId ? { model_connection_id: modelId } : {}),
  }
  const enabled = authenticated && active
  const summary = useQuery({
    queryKey: ['analytics-summary', filters],
    queryFn: () => analyticsApi.summary(filters),
    enabled,
  })
  const calls = useInfiniteQuery({
    queryKey: ['analytics-calls', filters],
    queryFn: ({ pageParam }: { pageParam: string | null }) =>
      analyticsApi.calls(filters, 50, pageParam ?? undefined),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
  })
  const hardware = useQuery({
    queryKey: ['analytics-hardware'],
    queryFn: analyticsApi.hardware,
    enabled,
  })
  const refreshing = summary.isFetching || calls.isFetching || hardware.isFetching
  const items = Array.from(
    new Map(
      (calls.data?.pages.flatMap((page) => page.items) ?? []).map((call) => [
        call.attempt_id,
        call,
      ]),
    ).values(),
  )
  return (
    <div className="page-content analytics-page">
      <PageHeading
        title="数据看板"
        action={
          <Button
            disabled={!authenticated || refreshing}
            onClick={() =>
              void Promise.all([summary.refetch(), calls.refetch(), hardware.refetch()])
            }
          >
            <RefreshCw size={14} className={refreshing ? 'spinning' : ''} />
            刷新读数
          </Button>
        }
      >
        真实调用、计量覆盖与服务端当前硬件读数。
      </PageHeading>
      {!authenticated ? (
        <EmptyState icon={BarChart3} title="看板服务尚未连接">
          登录工作台后读取真实指标。
        </EmptyState>
      ) : (
        <>
          <form className="analytics-filters" onSubmit={(event) => event.preventDefault()}>
            <label>
              时间窗
              <select
                value={window}
                onChange={(event) =>
                  setWindow(event.target.value as AnalyticsQueryFilters['window'])
                }
              >
                <option value="24h">过去 24 小时</option>
                <option value="7d">过去 7 天</option>
                <option value="30d">过去 30 天</option>
              </select>
            </label>
            <label>
              工作区
              <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
                <option value="">全部工作区</option>
                {workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              模型连接
              <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
                <option value="">全部模型连接</option>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
          </form>
          {summary.isError ? (
            <ReadError error={summary.error} retry={() => void summary.refetch()} />
          ) : summary.data ? (
            <>
              <p className="analytics-freshness">
                {utcLabel(summary.data.filters.start_at)} — {utcLabel(summary.data.filters.end_at)}{' '}
                · 生成于 {utcLabel(summary.data.generated_at)}
              </p>
              <OverviewMetrics metrics={summary.data.metrics} cost={summary.data.cost} />
              <AnalyticsSection
                title="模型调用分布"
                description={`按 UTC ${summary.data.granularity === 'hour' ? '小时' : '日'}聚合，仅展示存在实际尝试的时间桶，不填充空桶或平滑值。`}
              >
                {summary.data.series.length && active ? (
                  <Suspense fallback={<p className="page-note">正在加载真实时间桶图表…</p>}>
                    <AnalyticsTrend summary={summary.data} />
                  </Suspense>
                ) : (
                  <div className="analytics-empty">当前筛选没有可显示的调用时间桶。</div>
                )}
              </AnalyticsSection>
              <details className="analytics-source analytics-summary-notes">
                <summary>统计口径与运行记录范围</summary>
                <p>
                  分位数方法：连续线性插值。P50/P95
                  均附实际可计算样本数。模型尝试是调用分母，运行与工具操作分别计数。
                </p>
                <p>
                  运行：{countLabel(summary.data.runs.total)}；工具操作：
                  {countLabel(summary.data.operations.total)}。
                </p>
                <ul>
                  {summary.data.notes.map((note, index) => (
                    <li key={`${note}:${index}`}>{note}</li>
                  ))}
                </ul>
              </details>
            </>
          ) : (
            <p className="page-note">正在读取真实调用统计…</p>
          )}
          <AnalyticsSection
            title="调用明细与追溯"
            description="每行对应一次模型尝试；用量缺失不等于消耗为零。请求平均输出速率包含请求阶段开销。"
          >
            {calls.isError && <ReadError error={calls.error} retry={() => void calls.refetch()} />}{' '}
            {items.length ? (
              <>
                <CallsTable items={items} onOpenRun={onOpenRun} />
                {calls.hasNextPage && (
                  <div className="analytics-load-more">
                    <Button
                      disabled={calls.isFetchingNextPage}
                      onClick={() => void calls.fetchNextPage()}
                    >
                      {calls.isFetchingNextPage ? '正在读取…' : '加载更多真实调用'}
                    </Button>
                  </div>
                )}
              </>
            ) : !calls.isError ? (
              <div className="analytics-empty">
                {calls.isPending ? '正在读取调用明细…' : '当前筛选没有模型调用记录。'}
              </div>
            ) : null}
          </AnalyticsSection>
          {hardware.isError ? (
            <ReadError error={hardware.error} retry={() => void hardware.refetch()} />
          ) : hardware.data ? (
            <HardwarePanel snapshot={hardware.data} />
          ) : (
            <p className="page-note">正在读取实际硬件状态…</p>
          )}
        </>
      )}
    </div>
  )
}
