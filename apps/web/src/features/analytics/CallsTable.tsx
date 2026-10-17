import { useState } from 'react'
import type { UsageCall } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { countLabel, durationLabel, numberLabel, utcLabel, callStatusLabels } from './ui'

export function CallsTable({
  items,
  onOpenRun,
}: {
  items: UsageCall[]
  onOpenRun: (call: UsageCall) => Promise<void>
}) {
  const [opening, setOpening] = useState('')
  return (
    <div className="analytics-table-scroll">
      <table className="analytics-calls">
        <caption className="sr-only">
          当前时间窗和筛选中的真实模型调用，每行对应一次模型 attempt
        </caption>
        <thead>
          <tr>
            <th>时间 / 工作区</th>
            <th>模型</th>
            <th>状态</th>
            <th>输入 / 输出 tokens</th>
            <th>首段可见延迟</th>
            <th>请求平均输出速率</th>
            <th>追溯</th>
          </tr>
        </thead>
        <tbody>
          {items.map((call) => (
            <tr key={call.attempt_id}>
              <td>
                {utcLabel(call.created_at)}
                <small>{call.workspace_name}</small>
              </td>
              <td>
                {call.model_name ?? '模型名称未知'}
                <small>{call.model_identifier ?? '模型标识未知'}</small>
              </td>
              <td>
                <Status
                  tone={
                    call.status === 'succeeded'
                      ? 'good'
                      : call.status === 'failed'
                        ? 'danger'
                        : 'neutral'
                  }
                >
                  {callStatusLabels[call.status] ?? call.status}
                </Status>
              </td>
              <td>
                {countLabel(call.input_tokens_total)} / {countLabel(call.output_tokens_total)}
                <small>{call.usage_known ? '提供方用量已知' : '用量缺失或未知'}</small>
              </td>
              <td>{durationLabel(call.first_visible_ms)}</td>
              <td>
                {numberLabel(call.request_output_tokens_per_second)}
                {call.request_output_tokens_per_second !== null && ' tok/s'}
              </td>
              <td>
                <details className="analytics-call-trace">
                  <summary>查看追溯</summary>
                  <dl>
                    <dt>Run</dt>
                    <dd>{call.run_id}</dd>
                    <dt>Step</dt>
                    <dd>{call.step_id}</dd>
                    <dt>Attempt</dt>
                    <dd>
                      {call.attempt_id} · 第 {call.attempt_number} 次
                    </dd>
                    <dt>用量记录</dt>
                    <dd>{call.usage_record_id ?? '无记录'}</dd>
                    <dt>计量来源</dt>
                    <dd>{call.usage_source ?? '未知'}</dd>
                    <dt>请求时长</dt>
                    <dd>{durationLabel(call.duration_ms)}</dd>
                    <dt>缓存读 / 写 tokens</dt>
                    <dd>
                      {countLabel(call.cache_read_input_tokens)} /{' '}
                      {countLabel(call.cache_write_input_tokens)}
                    </dd>
                    <dt>推理 tokens</dt>
                    <dd>{countLabel(call.reasoning_tokens)}</dd>
                    <dt>结束原因</dt>
                    <dd>{call.finish_reason ?? '未记录'}</dd>
                    <dt>费用</dt>
                    <dd>未知 · {call.cost.reason}</dd>
                    {call.error && (
                      <>
                        <dt>失败原因</dt>
                        <dd>
                          {call.error.code} · {call.error.message}
                        </dd>
                      </>
                    )}
                  </dl>
                  <Button
                    disabled={!!opening}
                    onClick={() => {
                      setOpening(call.attempt_id)
                      void onOpenRun(call).finally(() => setOpening(''))
                    }}
                  >
                    {opening === call.attempt_id ? '正在定位…' : '查看关联对话'}
                  </Button>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
