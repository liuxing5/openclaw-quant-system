/**
 * ECharts 轻封装：按需注册（tree-shaking），随容器尺寸自动 resize。
 *
 * A 股配色约定：涨 = rose（红），跌 = emerald（绿），平 = slate。
 */
import React, { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import {
  BarChart,
  GaugeChart,
  LineChart,
  PieChart,
  RadarChart,
  ScatterChart,
} from 'echarts/charts'
import {
  DataZoomComponent,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  RadarChart,
  GaugeChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  RadarComponent,
  TitleComponent,
  MarkLineComponent,
  MarkPointComponent,
  DataZoomComponent,
  DatasetComponent,
  VisualMapComponent,
  CanvasRenderer,
])

export const UP = '#f43f5e'
export const UP_SOFT = '#fb7185'
export const DOWN = '#10b981'
export const DOWN_SOFT = '#34d399'
export const FLAT = '#94a3b8'
export const ACCENT = '#6366f1'

/** 常用 tooltip / 轴线样式，保证深浅主题下都可读 */
export const TIP = {
  trigger: 'axis',
  backgroundColor: 'rgba(15,23,42,0.92)',
  borderWidth: 0,
  textStyle: { color: '#f8fafc', fontSize: 12 },
  valueFormatter: (v) => (typeof v === 'number' ? v.toLocaleString('zh-CN') : v),
}

export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
  axisLabel: { color: '#94a3b8', fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
}

export default function Chart({ option, height = 260, className = '', onEvents = {} }) {
  const hostRef = useRef(null)
  const chartRef = useRef(null)
  const eventsRef = useRef(onEvents)
  eventsRef.current = onEvents

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined
    const chart = echarts.init(host, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(host)
    return () => {
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (chart && option) chart.setOption(option, true)
  }, [option])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return undefined
    const bound = []
    for (const [ev, fn] of Object.entries(eventsRef.current || {})) {
      if (typeof fn !== 'function') continue
      chart.on(ev, fn)
      bound.push([ev, fn])
    }
    return () => bound.forEach(([ev, fn]) => chart.off(ev, fn))
  }, [option])

  return <div ref={hostRef} style={{ height }} className={className} />
}

/** 迷你走势线（纯 SVG，避免为每张卡片各起一个 ECharts 实例） */
export function Sparkline({ points = [], height = 34, className = '', color = null }) {
  if (!points || points.length < 2) {
    return <div style={{ height }} className={className} />
  }
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const w = 100
  const h = 100
  const step = w / (points.length - 1)
  const coords = points.map((v, i) => [i * step, h - ((v - min) / span) * (h - 6) - 3])
  const d = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c[0].toFixed(2)},${c[1].toFixed(2)}`).join(' ')
  const area = `${d} L${w},${h} L0,${h} Z`
  const stroke = color || (points[points.length - 1] >= points[0] ? UP : DOWN)
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      style={{ height }}
      className={className}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`spark-${stroke.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#spark-${stroke.replace('#', '')})`} />
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
