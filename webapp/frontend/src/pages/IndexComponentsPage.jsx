/**
 * 指数成分（/index/components）：选指数 → 看成分股列表（含权重条）。
 *
 * 指数列表复用 `GET /index/summary` 的 items，保证与「指数总览」页看到的 6 只指数完全一致；
 * 成分列表走 `GET /index/components/{ts_code}?page&size`（服务端分页）。
 *
 * 关键约束：本地库没有指数公司官方成分与权重，后端只能按总市值取前 N 近似，
 * 因此只要响应里带 `meta.approx`，本页必须显式亮出「按总市值近似」徽标 + 说明文案，
 * 不能把近似结果伪装成官方成分名单。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { AsyncBlock, Badge, Card, Pagination, PageShell, SectionHeader, Select } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone, ratioPct } from '../lib/format.js'

const POLL_MS = 60_000
const PAGE_SIZES = [10, 20, 50]

/** summary 尚未返回时的兜底池：页面仍可切换、表格仍可请求 */
const FALLBACK_INDEXES = [
  { ts_code: '000001.SH', name: '上证指数' },
  { ts_code: '399001.SZ', name: '深证成指' },
  { ts_code: '399006.SZ', name: '创业板指' },
  { ts_code: '000300.SH', name: '沪深300' },
  { ts_code: '000905.SH', name: '中证500' },
  { ts_code: '000688.SH', name: '科创50' },
]

function toNum(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function pickItems(data) {
  if (Array.isArray(data)) return data.filter((x) => x && typeof x === 'object')
  if (!data || typeof data !== 'object') return []
  const list = data.items || data.indexes || data.list || []
  return Array.isArray(list) ? list.filter((x) => x && typeof x === 'object') : []
}

function pick(row, keys) {
  if (!row) return null
  for (const k of keys) {
    const v = row[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return null
}

/**
 * 权重口径不可预知：后端可能给 3.21（百分数）也可能给 0.0321（小数）。
 * 绝对值 ≤1 视作小数，否则按百分数，避免把 3.21% 显示成 321%。
 */
function fmtWeight(v) {
  const n = toNum(v)
  if (n === null) return '—'
  return Math.abs(n) <= 1 ? ratioPct(n, 2) : `${fmtNum(n, 2)}%`
}

/** 权重条：宽度按当前页最大权重归一，保证同一屏内可横向比较 */
function WeightBar({ value, max }) {
  const n = toNum(value)
  if (n === null) return <span className="text-slate-400">—</span>
  const w = max > 0 ? Math.max(3, Math.min(100, (Math.abs(n) / max) * 100)) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full bg-brand-500 dark:bg-brand-400" style={{ width: `${w}%` }} />
      </div>
      <span className="tabular-nums text-[11px] text-slate-500 dark:text-slate-400">{fmtWeight(n)}</span>
    </div>
  )
}

export default function IndexComponentsPage() {
  const summary = useApi(() => api.get('/index/summary'), [])
  const [code, setCode] = useState(FALLBACK_INDEXES[0].ts_code)
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(20)

  const indexes = useMemo(() => pickItems(summary.data), [summary.data])
  const options = indexes.length ? indexes : FALLBACK_INDEXES

  // summary 返回后把选中项对齐到真实指数列表（接口未上线时停留在兜底代码上）
  useEffect(() => {
    if (!indexes.length) return
    setCode((prev) => (indexes.some((i) => i.ts_code === prev) ? prev : indexes[0].ts_code))
  }, [indexes])

  const list = useApi(
    () => api.get(`/index/components/${code}`, { page, size }),
    [code, page, size],
  )
  const rows = useMemo(() => pickItems(list.data), [list.data])
  const total = Number(list.data?.total ?? rows.length) || 0
  const meta = list.data?.meta || null
  const approx = Boolean(meta && (meta.approx === true || meta.is_approx === true))

  // 60 秒静默轮询两份数据，卸载时清理，避免定时器泄漏
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      summary.reload()
      list.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [summary.reload, list.reload])

  const active = options.find((o) => o.ts_code === code) || options[0]
  const maxWeight = rows.reduce((m, r) => Math.max(m, Math.abs(toNum(pick(r, ['weight', 'ratio', 'weight_pct'])) || 0)), 0)

  return (
    <PageShell
      title="指数成分"
      subtitle="选择指数查看成分股权重与当日表现"
      actions={
        <div className="flex items-center gap-2">
          <Select
            value={code}
            onChange={(v) => {
              setCode(v)
              setPage(1)
            }}
            options={options.map((o) => ({ value: o.ts_code, label: `${o.name || o.ts_code} ${o.ts_code}` }))}
            aria-label="选择指数"
          />
          <Select
            value={size}
            onChange={(v) => {
              setSize(Number(v))
              setPage(1)
            }}
            options={PAGE_SIZES.map((s) => ({ value: s, label: `每页 ${s} 只` }))}
            aria-label="每页条数"
          />
        </div>
      }
    >
      <div className="space-y-4">
        {/* 指数概览条：summary 挂了也不影响下方成分表，错误只在本块内呈现 */}
        <Card>
          <AsyncBlock
            loading={summary.loading && !summary.data}
            error={!summary.data ? summary.error : null}
            onRetry={summary.reload}
            loadingLabel="指数概览加载中…"
          >
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <div>
                <p className="text-xs text-slate-500 dark:text-slate-400">{active?.name || active?.ts_code}</p>
                <p className={`text-lg font-semibold tabular-nums ${pctTone(active?.pct_chg)}`}>
                  {fmtNum(active?.close, 2)}
                  <span className="ml-2 text-sm">{fmtPct(active?.pct_chg)}</span>
                </p>
              </div>
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                <span>今开 <b className="tabular-nums text-slate-700 dark:text-slate-200">{fmtNum(active?.open, 2)}</b></span>
                <span>最高 <b className="tabular-nums text-slate-700 dark:text-slate-200">{fmtNum(active?.high, 2)}</b></span>
                <span>最低 <b className="tabular-nums text-slate-700 dark:text-slate-200">{fmtNum(active?.low, 2)}</b></span>
                <span>成分 <b className="tabular-nums text-slate-700 dark:text-slate-200">{fmtNum(total, 0)}</b> 只</span>
              </div>
              {approx ? (
                <Badge
                  tone="amber"
                  className="cursor-help"
                  title="本地库没有指数公司下发的成分与权重，列表按成分股总市值从大到小取前 N 只近似。"
                >
                  ⓘ 按总市值近似
                </Badge>
              ) : null}
            </div>

            {approx ? (
              <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                {meta?.note ||
                  meta?.description ||
                  '本列表按成分股总市值从大到小取前 N 只近似生成，非指数公司官方成分名单；权重为市值占比估算值，与官方权重可能存在差异，仅供建模与研究参考。'}
              </p>
            ) : null}
          </AsyncBlock>
        </Card>

        <Card>
          <SectionHeader
            title="成分列表"
            subtitle={`${active?.name || ''} · 服务端分页（第 ${page} 页 / 每页 ${size} 只）`}
            badge={
              meta && meta.method ? (
                <Badge tone="blue" title={String(meta.method)}>
                  {String(meta.method)}
                </Badge>
              ) : null
            }
          />
          <div className="mt-3">
            <AsyncBlock
              loading={list.loading && !list.data}
              error={!list.data ? list.error : null}
              onRetry={list.reload}
              loadingLabel="成分数据加载中…"
              empty={Boolean(list.data) && rows.length === 0}
              emptyText="该指数暂无成分数据"
              emptyHint={`本地未采集 ${code} 的成分股，或当前页无记录`}
            >
              <div className="max-h-[560px] overflow-x-auto overflow-y-auto thin-scroll">
                <table className="w-full min-w-[640px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="sticky top-0 z-10 border-b border-slate-200 bg-white py-2 pr-3 font-medium dark:border-slate-700 dark:bg-slate-900">
                        代码
                      </th>
                      <th className="sticky top-0 z-10 border-b border-slate-200 bg-white py-2 pr-3 font-medium dark:border-slate-700 dark:bg-slate-900">
                        名称
                      </th>
                      <th className="sticky top-0 z-10 border-b border-slate-200 bg-white py-2 pr-3 text-right font-medium dark:border-slate-700 dark:bg-slate-900">
                        总市值
                      </th>
                      <th className="sticky top-0 z-10 border-b border-slate-200 bg-white py-2 pr-3 text-right font-medium dark:border-slate-700 dark:bg-slate-900">
                        涨跌幅
                      </th>
                      <th className="sticky top-0 z-10 border-b border-slate-200 bg-white py-2 font-medium dark:border-slate-700 dark:bg-slate-900">
                        权重
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const pct = toNum(pick(r, ['pct_chg', 'change_pct', 'pctChg']))
                      return (
                        <tr
                          key={r.ts_code || r.code || `${i}-${r.name}`}
                          className="border-b border-slate-100 last:border-0 dark:border-slate-800/70"
                        >
                          <td className="py-2 pr-3 tabular-nums text-slate-500 dark:text-slate-400">
                            {pick(r, ['ts_code', 'code', 'symbol']) ?? '—'}
                          </td>
                          <td className="py-2 pr-3 font-medium text-slate-700 dark:text-slate-200">
                            {pick(r, ['name', 'stock_name', 'sec_name']) ?? '—'}
                          </td>
                          <td className="py-2 pr-3 text-right tabular-nums text-slate-600 dark:text-slate-300">
                            {fmtAmount(toNum(pick(r, ['total_market_cap', 'market_cap', 'cap'])) ?? undefined)}
                          </td>
                          <td className={`py-2 pr-3 text-right font-medium tabular-nums ${pctTone(pct)}`}>
                            {fmtPct(pct)}
                          </td>
                          <td className="py-2">
                            <WeightBar value={pick(r, ['weight', 'ratio', 'weight_pct'])} max={maxWeight} />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={size} total={total} onChange={setPage} />
            </AsyncBlock>
          </div>
        </Card>
      </div>
    </PageShell>
  )
}
