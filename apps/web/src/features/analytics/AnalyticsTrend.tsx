import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import type { BarSeriesOption } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import type { GridComponentOption, TooltipComponentOption } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { AnalyticsSummary } from '../../api/types'
import { countLabel, utcLabel } from './ui'

echarts.use([BarChart, GridComponent, TooltipComponent, SVGRenderer])
type Option = echarts.ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption>
export default function AnalyticsTrend({ summary }: { summary: AnalyticsSummary }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = container.current
    if (!element) return
    const chart = echarts.init(element, undefined, { renderer: 'svg' })
    const render = () => {
      const style = getComputedStyle(element)
      const foreground = style.getPropertyValue('--fg').trim()
      const muted = style.getPropertyValue('--muted').trim()
      const border = style.getPropertyValue('--border').trim()
      const accent = style.getPropertyValue('--accent').trim()
      const option: Option = {
        animation: false,
        useUTC: true,
        grid: { left: 45, right: 20, top: 18, bottom: 42 },
        tooltip: {
          trigger: 'axis',
          renderMode: 'richText',
          valueFormatter: (value) =>
            `${typeof value === 'number' ? value.toLocaleString('zh-CN') : String(value)} 次`,
        },
        xAxis: {
          type: 'time',
          min: Math.min(
            Date.parse(summary.filters.start_at),
            ...summary.series.map((point) => Date.parse(point.bucket_start)),
          ),
          max: Math.max(
            Date.parse(summary.filters.end_at),
            ...summary.series.map((point) => Date.parse(point.bucket_end)),
          ),
          axisLine: { lineStyle: { color: border } },
          axisTick: { show: false },
          axisLabel: {
            color: muted,
            hideOverlap: true,
            formatter: (value: number) =>
              new Date(value).toLocaleString('zh-CN', {
                timeZone: 'UTC',
                month: '2-digit',
                day: '2-digit',
                ...(summary.granularity === 'hour' ? { hour: '2-digit' } : {}),
                hour12: false,
              }),
          },
        },
        yAxis: {
          type: 'value',
          min: 0,
          minInterval: 1,
          axisLabel: { color: muted },
          splitLine: { lineStyle: { color: border } },
          axisLine: { show: false },
        },
        series: [
          {
            name: '模型调用',
            type: 'bar',
            barMaxWidth: 28,
            itemStyle: { color: accent },
            data: summary.series.map((point) => [
              Date.parse(point.bucket_start),
              point.metrics.attempts,
            ]),
          },
        ],
        textStyle: { color: foreground, fontFamily: style.fontFamily, fontSize: 11 },
      }
      chart.setOption(option, true)
      chart.resize()
    }
    render()
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(element)
    const theme = new MutationObserver(render)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const scheme = window.matchMedia('(prefers-color-scheme: dark)')
    scheme.addEventListener('change', render)
    return () => {
      resize.disconnect()
      theme.disconnect()
      scheme.removeEventListener('change', render)
      chart.dispose()
    }
  }, [summary])
  return (
    <>
      <div
        ref={container}
        className="analytics-trend"
        role="img"
        aria-label={`当前筛选内按UTC${summary.granularity === 'hour' ? '小时' : '日'}统计的模型调用数，精确数值见下方时间桶数据。`}
      />
      <details className="analytics-source">
        <summary>查看实际时间桶数据</summary>
        <div className="analytics-table-scroll">
          <table>
            <caption className="sr-only">实际模型调用时间桶</caption>
            <thead>
              <tr>
                <th>开始时间（UTC）</th>
                <th>结束时间（UTC）</th>
                <th>模型调用</th>
                <th>已知用量覆盖</th>
              </tr>
            </thead>
            <tbody>
              {summary.series.map((point) => (
                <tr key={point.bucket_start}>
                  <td>{utcLabel(point.bucket_start)}</td>
                  <td>{utcLabel(point.bucket_end)}</td>
                  <td>{countLabel(point.metrics.attempts)}</td>
                  <td>
                    {point.metrics.known_usage_attempts} / {point.metrics.attempts}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  )
}
