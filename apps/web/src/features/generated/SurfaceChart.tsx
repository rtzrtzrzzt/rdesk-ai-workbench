import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import type { BarSeriesOption, LineSeriesOption } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import type { GridComponentOption, TooltipComponentOption } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { ChartProps } from './types'
echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, SVGRenderer])
type Option = echarts.ComposeOption<
  BarSeriesOption | LineSeriesOption | GridComponentOption | TooltipComponentOption
>
export default function SurfaceChart({ chart }: { chart: ChartProps }) {
  const element = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!element.current) return
    const container = element.current
    const instance = echarts.init(container, undefined, { renderer: 'svg' })
    const render = () => {
      const style = getComputedStyle(container)
      const muted = style.getPropertyValue('--muted').trim()
      const border = style.getPropertyValue('--border').trim()
      const accent = style.getPropertyValue('--accent').trim()
      const options: Option = {
        animation: false,
        grid: { left: 65, right: 25, top: 35, bottom: 65 },
        tooltip: {
          trigger: 'axis',
          renderMode: 'richText',
          valueFormatter: (value) =>
            typeof value === 'number'
              ? `${value.toLocaleString('zh-CN')}${chart.unit ? ` ${chart.unit}` : ''}`
              : '缺失',
        },
        xAxis: {
          type: 'category',
          name: chart.x_label,
          nameLocation: 'middle',
          nameGap: 40,
          data: chart.points.map((point) => point.label),
          axisLabel: { color: muted, hideOverlap: true },
          axisLine: { lineStyle: { color: border } },
        },
        yAxis: {
          type: 'value',
          name: `${chart.y_label}${chart.unit ? `（${chart.unit}）` : ''}`,
          nameTextStyle: { color: muted },
          axisLabel: { color: muted },
          splitLine: { lineStyle: { color: border } },
        },
        series: [
          chart.kind === 'line'
            ? {
                type: 'line',
                connectNulls: false,
                smooth: false,
                symbolSize: 5,
                data: chart.points.map((point) => point.value),
                itemStyle: { color: accent },
              }
            : {
                type: 'bar',
                barMaxWidth: 36,
                data: chart.points.map((point) => point.value),
                itemStyle: { color: accent },
              },
        ],
        textStyle: {
          fontFamily: style.fontFamily,
          fontSize: 11,
          color: style.getPropertyValue('--fg').trim(),
        },
      }
      instance.setOption(options, true)
      instance.resize()
    }
    render()
    const resize = new ResizeObserver(() => instance.resize())
    resize.observe(container)
    const theme = new MutationObserver(render)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const scheme = matchMedia('(prefers-color-scheme: dark)')
    scheme.addEventListener('change', render)
    return () => {
      resize.disconnect()
      theme.disconnect()
      scheme.removeEventListener('change', render)
      instance.dispose()
    }
  }, [chart])
  return (
    <div
      ref={element}
      className="wb-surface-chart"
      role="img"
      aria-label={`${chart.kind === 'line' ? '折线图' : '柱状图'}：${chart.x_label}、${chart.y_label}。精确值与缺失值见数据表。`}
    />
  )
}
