/**
 * 实时异动（/anomaly）：盘中异动事件时间流。
 *
 * 为什么做成时间轴而不是表格：异动是「事件」而不是「指标」，盯盘时要按发生顺序
 * 从新到旧读；类型徽标 + 说明文字比列对齐更能交代一次异动的上下文。
 * 30 秒静默轮询保证流式更新，「立即扫描」触发后端即时重算并提示新增条数。
 */
import { useEffect, useMemo, useState } from 'react'
import { AsyncBlock, Badge, Button, Card, EmptyState, PageShell, Pagination, SectionHeader, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDate, fmtNum, fmtPct, fmtTime, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const POLL_MS = 30_000
const PAGE_SIZE = 20

const SCOPE_TABS = [
  { value: 'all_market', label: '全市场（含板块）' },
  { value: 'symbol', label: '个股' },
]

/** kind -> 中文名 + 徽标色 + 时间轴圆点色（涨 rose / 跌 emerald / 量能 amber / 结构 brand） */
const KIND_META = {
  rapid_rise: { label: '急拉', tone: 'rose', dot: 'bg-rose-500' },
  volume_spike: { label: '放量', tone: 'amber', dot: 'bg-amber-500' },
  limit_up: { label: '涨停', tone: 'rose', dot: 'bg-rose-600' },
  limit_down: { label: '跌停', tone: 'emerald', dot: 'bg-emerald-500' },
  turnover_high: { label: '高换手', tone: 'amber', dot: 'bg-amber-400' },
  new_high: { label: '新高', tone: 'blue', dot: 'bg-brand-500' },
  board_surge: { label: '板块大涨', tone: 'rose', dot: 'bg-rose-400' },
}
const KIND_UNKNOWN = { label: '其他异动', tone: 'slate', dot: 'bg-slate-400' }

const metaOf = (kind) => KIND_META[kind] || { ...KIND_UNKNOWN, label: kind || KIND_UNKNOWN.label }

/** 兼容字段别名：契约字段优先，历史 schema 字段兜底，避免后端口径微调导致整页空 */
function pick(raw, main, alias) {
  const v = raw[main]
  return v === undefined || v === null ? raw[alias] : v
}

function normalize(raw, idx) {
  const tsCode = pick(raw, 'ts_code', 'target') || ''
  return {
    id: raw.id || `${tsCode}-${idx}`,
    code: tsCode,
    name: raw.name || tsCode || '—',
    kind: pick(raw, 'kind', 'event_type') || '',
    price: raw.price,
    pct: pick(raw, 'pct_chg', 'change_pct'),
    detail: raw.detail || '',
    at: pick(raw, 'occurred_at', 'detected_at') || raw.data_timestamp || null,
    scope: raw.scope || 'symbol',
  }
}

function EventRow({ ev, isLast }) {
  const meta = metaOf(ev.kind)
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      {!isLast ? (
        <span
          aria-hidden="true"
          className="absolute left-[4.5rem] top-5 bottom-0 w-px bg-slate-200 dark:bg-slate-800"
        />
      ) : null}

      {/* 时间列：事件流的“轴刻度” */}
      <div className="w-14 shrink-0 pt-0.5 text-right">
        <p className="text-xs font-medium tabular-nums text-slate-600 dark:text-slate-300">{fmtTime(ev.at)}</p>
        <p className="text-[10px] tabular-nums text-slate-400 dark:text-slate-500">{fmtDate(ev.at)}</p>
      </div>

      <span
        aria-hidden="true"
        className={`relative z-10 mt-2 size-2.5 shrink-0 rounded-full ring-4 ring-white dark:ring-slate-900 ${meta.dot}`}
      />

      <Card className="min-w-0 flex-1 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Badge tone={meta.tone}>{meta.label}</Badge>
            <span className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{ev.name}</span>
            <span className="shrink-0 text-[11px] tabular-nums text-slate-400 dark:text-slate-500">{ev.code}</span>
            {ev.scope !== 'symbol' ? <Badge tone="slate">板块</Badge> : null}
          </div>
          <div className="flex shrink-0 items-center gap-3 tabular-nums">
            <span className="text-sm font-medium text-slate-700 dark:text-slate-200">
              {ev.price === null || ev.price === undefined ? '—' : fmtNum(ev.price, 2)}
            </span>
            <span className={`text-sm font-semibold ${pctTone(ev.pct)}`}>{fmtPct(ev.pct)}</span>
          </div>
        </div>
        {ev.detail ? (
          <p className="mt-1.5 text-xs leading-5 text-slate-500 dark:text-slate-400">{ev.detail}</p>
        ) : null}
      </Card>
    </li>
  )
}

export default function AnomalyPage() {
  const [scope, setScope] = useState('all_market')
  const [page, setPage] = useState(1)
  const [scanning, setScanning] = useState(false)
  const [scannedAt, setScannedAt] = useState(null)

  const { data, loading, error, reload } = useApi(
    () =>
      api.get('/anomaly/events', {
        page,
        size: PAGE_SIZE,
        scope,
        all_market: scope === 'all_market' ? 1 : 0,
      }),
    [page, scope],
  )

  // 轮询：异动是盘中高频信息，30 秒静默刷新一次；隐藏标签页不空转
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const items = useMemo(() => {
    const rows = (data?.items || []).map(normalize)
    // 后端默认按时间倒序，这里再兜一次底，保证「最新在最上」
    return rows.sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')))
  }, [data])

  const lastScan = scannedAt || data?.last_scan_at || data?.scanned_at || null

  const scan = async () => {
    setScanning(true)
    try {
      const r = await api.post('/anomaly/events/scan', { scope })
      const scanned = r?.scanned ?? r?.scanned_count ?? r?.total
      const added = r?.new ?? r?.new_count ?? r?.added
      const parts = []
      if (scanned !== undefined && scanned !== null) parts.push(`扫描 ${scanned} 条`)
      if (added !== undefined && added !== null) parts.push(`新增异动 ${added} 条`)
      toast.success('扫描完成', parts.length ? parts.join('，') : '已按最新行情重新计算异动')
      setScannedAt(new Date().toISOString())
      reload()
    } catch (e) {
      // 失败不吞错：按钮保持可用，用户可直接再次点击重试
      toast.error('扫描失败', e.message)
    } finally {
      setScanning(false)
    }
  }

  return (
    <PageShell
      title="实时异动"
      subtitle={`数据日 ${data?.trade_date || '—'}（最近交易日快照）· 共 ${data?.total ?? '—'} 条事件 · 30 秒自动刷新`}
      actions={
        <div className="flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
          <span>
            最近扫描：
            <b className="font-medium tabular-nums text-slate-600 dark:text-slate-300">
              {lastScan ? fmtTime(lastScan) : '—'}
            </b>
          </span>
          <Button size="sm" loading={scanning} onClick={scan}>
            立即扫描
          </Button>
        </div>
      }
    >
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          tabs={SCOPE_TABS}
          value={scope}
          onChange={(v) => {
            setScope(v)
            setPage(1)
          }}
        />
        <p className="text-[11px] text-slate-500 dark:text-slate-400">
          按发生时间倒序 · 每页 {PAGE_SIZE} 条 · 行情与异动由后端扫描任务实时计算
        </p>
      </Card>

      {/* 首屏才走三态；已有数据时轮询只做静默刷新，避免整页闪烁 */}
      {!data ? (
        <AsyncBlock loading={loading} error={error} onRetry={reload} loadingLabel="异动事件加载中…" />
      ) : (
        <div className="space-y-4">
          <Card>
            <SectionHeader
              title="异动事件流"
              subtitle={`${SCOPE_TABS.find((t) => t.value === scope)?.label || ''} · 最新事件在最上方`}
              badge={<Badge tone="blue">{items.length} 条 / 本页</Badge>}
            />

            {items.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  text="暂无异动事件"
                  hint={error ? `数据加载失败：${error.message}` : '可点击右上角「立即扫描」主动计算一次盘中异动'}
                />
              </div>
            ) : (
              <ol className="mt-4">
                {items.map((ev, i) => (
                  <EventRow key={ev.id} ev={ev} isLast={i === items.length - 1} />
                ))}
              </ol>
            )}

            <Pagination page={page} pageSize={PAGE_SIZE} total={data.total || 0} onChange={setPage} />
          </Card>
        </div>
      )}
    </PageShell>
  )
}
