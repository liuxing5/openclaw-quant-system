/**
 * 选股策略（/strategy、/strategy/:id）：
 *
 * 顶部运行统计磁贴 → 策略卡片池（参数 chips / 启停开关 / 立即运行）→ 选中策略的
 * 命中明细 + 运行历史 + strategy_scans 扫描快照。
 *
 * 为什么做字段兼容：C1 各接口由并行后端单元实现，列表既可能回 `{items}` 也可能回裸数组，
 * 字段名在契约基础上可能存在别名，所以统一走 `asItems/pick` 归一化，避免任何一种实现下白屏。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  PageShell,
  Pagination,
  SectionHeader,
  StatTile,
  Tabs,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum, fmtPct, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const HITS_PAGE_SIZE = 10

/* --------------------------------------------------------------- 字段归一化 */

/** 列表响应可能是 `{items:[...]}`，也可能是裸数组（契约未强制外壳） */
const asItems = (d) => (Array.isArray(d) ? d : (d && d.items) || [])

/** 空值/非数字统一回 null，交给 fmt* 输出「—」 */
const n = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

/** 按候选键取第一个存在的值（并行实现可能用别名） */
const pick = (o, keys) => {
  if (!o) return null
  for (const k of keys) {
    if (o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k]
  }
  return null
}

/** 毫秒 → 「12.3s」 */
const sec = (ms) => (ms === null ? '—' : `${(Number(ms) / 1000).toFixed(1)}s`)

const FAMILY_TONES = ['blue', 'emerald', 'amber', 'rose', 'slate']

/** run-stats 若被实现成运行流水数组，就地聚合成同等指标（保证磁贴不空） */
function normalizeStats(d) {
  if (!Array.isArray(d)) return d || {}
  const recent = d.filter((r) => {
    const t = new Date(pick(r, ['run_at', 'created_at', 'started_at'])).getTime()
    return Number.isFinite(t) && Date.now() - t < 30 * 86400000
  })
  const hits = recent.reduce((acc, r) => acc + (n(pick(r, ['pick_count', 'total_passed', 'hit_count'])) || 0), 0)
  const times = recent.map((r) => n(pick(r, ['elapsed_ms', 'duration_ms']))).filter((v) => v !== null)
  return {
    runs_30d: recent.length,
    total_hits_30d: hits,
    avg_elapsed_ms: times.length ? times.reduce((a, b) => a + b, 0) / times.length : null,
    last_run_at: pick(d[0] || {}, ['run_at', 'created_at', 'started_at']),
  }
}

/** 策略参数：对象 / 数组 / 字符串都渲染成 chips */
function paramChips(params) {
  if (params === null || params === undefined || params === '') return []
  if (Array.isArray(params)) {
    return params.map((p) => (typeof p === 'object' ? `${p.name ?? p.key ?? ''} ${p.value ?? ''}` : String(p)))
  }
  if (typeof params === 'object') {
    return Object.entries(params).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
  }
  return [String(params)]
}

const strategyOf = (s) => ({
  code: pick(s, ['code', 'strategy_code', 'id']),
  name: pick(s, ['name', 'title']) || '未命名策略',
  family: pick(s, ['family', 'strategy_type', 'brief_tag']),
  description: pick(s, ['description', 'desc']),
  params: s.params,
  enabled: s.enabled === undefined ? (pick(s, ['status']) === undefined ? true : Number(s.status) !== 0) : Boolean(s.enabled),
  bestHitRate: n(pick(s, ['best_hit_rate', 'hit_rate', 'win_rate'])),
  lastRun: s.last_run || null,
})

const runOf = (r) => ({
  id: pick(r, ['run_id', 'id']),
  at: pick(r, ['run_at', 'created_at', 'started_at', 'executed_at']),
  scanned: n(pick(r, ['total_scanned', 'scanned', 'candidate_count'])),
  passed: n(pick(r, ['total_passed', 'passed', 'pick_count', 'hit_count'])),
  ms: n(pick(r, ['elapsed_ms', 'duration_ms'])),
})

const hitOf = (h) => ({
  tsCode: pick(h, ['ts_code', 'stock_code', 'symbol', 'code']),
  name: pick(h, ['name', 'stock_name']),
  price: n(pick(h, ['price', 'close', 'latest_price'])),
  pct: n(pick(h, ['pct_chg', 'change_pct'])),
  reasons: Array.isArray(h.reasons) ? h.reasons : [],
})

/* --------------------------------------------------------------- 策略卡片 */

function StrategyCard({ item, index, running, toggling, onRun, onToggle, onOpen, active }) {
  const s = strategyOf(item)
  const last = s.lastRun ? runOf(s.lastRun) : null
  const chips = paramChips(s.params)
  return (
    <Card className={`flex flex-col ${active ? 'ring-2 ring-brand-500/60' : ''}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{s.name}</h3>
            <span className="font-mono text-[11px] text-slate-400">{s.code}</span>
            <Badge tone={FAMILY_TONES[index % FAMILY_TONES.length]}>{s.family || '策略'}</Badge>
            {s.bestHitRate !== null ? (
              <Badge tone="amber">胜率 {fmtNum(s.bestHitRate, 1)}%</Badge>
            ) : null}
          </div>
          {s.description ? (
            <p className="mt-1.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{s.description}</p>
          ) : null}
        </div>
        {/* 启停开关：走 PATCH /strategies/{code}，失败会 toast 并回滚重载 */}
        <button
          type="button"
          role="switch"
          aria-checked={s.enabled}
          aria-label={s.enabled ? '停用策略' : '启用策略'}
          disabled={toggling}
          onClick={() => onToggle(s)}
          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-60 ${
            s.enabled ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600'
          }`}
        >
          <span
            className={`inline-block size-4 rounded-full bg-white shadow transition-transform ${
              s.enabled ? 'translate-x-4' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>

      {chips.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {chips.slice(0, 6).map((c) => (
            <span
              key={c}
              className="rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400"
            >
              {c}
            </span>
          ))}
        </div>
      ) : null}

      <div className="mt-3 grid grid-cols-4 gap-1.5 text-center">
        {[
          { label: '最近运行', value: last?.at ? fmtDateTime(last.at) : '—' },
          { label: '扫描', value: last ? fmtNum(last.scanned, 0) : '—' },
          { label: '命中', value: last ? fmtNum(last.passed, 0) : '—' },
          { label: '耗时', value: last ? sec(last.ms) : '—' },
        ].map((m) => (
          <div key={m.label} className="rounded-lg bg-slate-50 py-1.5 dark:bg-slate-800/60">
            <p className="text-[10px] text-slate-400">{m.label}</p>
            <p className="truncate text-[11px] font-semibold tabular-nums text-slate-700 dark:text-slate-200">{m.value}</p>
          </div>
        ))}
      </div>

      <div className="mt-auto flex items-center gap-2 pt-3">
        <Button size="sm" loading={running} onClick={() => onRun(s)}>
          {running ? '运行中…' : '立即运行'}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onOpen(s)}>
          {active ? '收起明细' : '查看结果'}
        </Button>
        {!s.enabled ? <Badge tone="slate">已停用</Badge> : null}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------- 选中策略明细 */

function StrategyDetail({ code, reloadKey }) {
  const runsQ = useApi(() => api.get(`/strategies/${code}/runs`, { limit: 20 }), [code, reloadKey])
  const runs = useMemo(() => asItems(runsQ.data).map(runOf), [runsQ.data])

  const [focusRun, setFocusRun] = useState(null)
  const [page, setPage] = useState(1)
  // 切换策略时清空手动选中的运行批次，并回到命中明细第一页
  useEffect(() => {
    setFocusRun(null)
    setPage(1)
  }, [code])

  const runId = focusRun || runs[0]?.id || null
  const hitsQ = useApi(() => api.get(`/strategies/${code}/hits`, { run_id: runId, size: 200 }), [code, runId, reloadKey], {
    enabled: Boolean(code && runId),
  })
  const scansQ = useApi(() => api.get('/strategies/scans', { strategy: code, days: 30 }), [code, reloadKey], {
    enabled: Boolean(code),
  })

  const hits = useMemo(() => asItems(hitsQ.data).map(hitOf), [hitsQ.data])
  const scans = useMemo(() => asItems(scansQ.data), [scansQ.data])
  const pageHits = hits.slice((page - 1) * HITS_PAGE_SIZE, page * HITS_PAGE_SIZE)

  return (
    <div className="space-y-4">
      <Card>
        <SectionHeader
          title="命中明细"
          subtitle={`策略 ${code} · 每条命中给出量化理由，点击运行批次可切换回看`}
          badge={
            <Badge tone="blue">
              {hits.length ? `共 ${hits.length} 只` : '无命中'}
            </Badge>
          }
        />
        <AsyncBlock
          loading={hitsQ.loading && !hitsQ.data}
          error={hitsQ.error}
          onRetry={hitsQ.reload}
          loadingLabel="命中明细加载中"
          empty={!hitsQ.loading && !hitsQ.error && hits.length === 0}
          emptyText="该批次未命中个股"
          emptyHint={runId ? '可运行策略生成新的选股结果' : '尚无运行记录，点「立即运行」生成'}
        >
          <div className="mt-3 overflow-x-auto thin-scroll">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[11px] text-slate-400">
                  <th className="pb-1.5 font-medium">代码</th>
                  <th className="pb-1.5 font-medium">名称</th>
                  <th className="pb-1.5 text-right font-medium">现价</th>
                  <th className="pb-1.5 text-right font-medium">涨跌幅</th>
                  <th className="pb-1.5 font-medium">入选理由</th>
                </tr>
              </thead>
              <tbody>
                {pageHits.map((h, i) => (
                  <tr key={`${h.tsCode}-${i}`} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1.5 font-mono tabular-nums text-slate-500">{h.tsCode || '—'}</td>
                    <td className="py-1.5 font-medium text-slate-700 dark:text-slate-200">{h.name || '—'}</td>
                    <td className="py-1.5 text-right tabular-nums text-slate-600 dark:text-slate-300">
                      {h.price === null ? '—' : fmtNum(h.price, 2)}
                    </td>
                    <td className={`py-1.5 text-right font-medium tabular-nums ${pctTone(h.pct)}`}>
                      {fmtPct(h.pct)}
                    </td>
                    <td className="py-1.5">
                      <div className="flex flex-wrap gap-1">
                        {h.reasons.length ? (
                          h.reasons.map((r) => (
                            <span
                              key={r}
                              className="rounded-full border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400"
                            >
                              {r}
                            </span>
                          ))
                        ) : (
                          <span className="text-[11px] text-slate-400">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageSize={HITS_PAGE_SIZE} total={hits.length} onChange={setPage} />
        </AsyncBlock>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <SectionHeader title="近期运行历史" subtitle="最近 20 次跑批，点行切换命中批次" />
          <AsyncBlock
            loading={runsQ.loading && !runsQ.data}
            error={runsQ.error}
            onRetry={runsQ.reload}
            loadingLabel="运行历史加载中"
            empty={!runsQ.loading && !runsQ.error && runs.length === 0}
            emptyText="尚无运行记录"
            emptyHint="点上方「立即运行」跑一次即可"
          >
            <div className="mt-3 overflow-x-auto thin-scroll">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">时间</th>
                    <th className="pb-1.5 text-right font-medium">扫描</th>
                    <th className="pb-1.5 text-right font-medium">命中</th>
                    <th className="pb-1.5 text-right font-medium">耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr
                      key={r.id || r.at}
                      onClick={() => {
                        setFocusRun(r.id)
                        setPage(1)
                      }}
                      className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60 ${
                        r.id && r.id === runId ? 'bg-brand-50/60 dark:bg-brand-500/10' : ''
                      }`}
                    >
                      <td className="py-1.5 tabular-nums text-slate-600 dark:text-slate-300">{fmtDateTime(r.at)}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-500">{fmtNum(r.scanned, 0)}</td>
                      <td className="py-1.5 text-right font-medium tabular-nums text-rose-600 dark:text-rose-400">
                        {fmtNum(r.passed, 0)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-slate-500">{sec(r.ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </AsyncBlock>
        </Card>

        <Card>
          <SectionHeader
            title="扫描快照（strategy_scans）"
            subtitle="每日全市场扫描的漏斗统计与当时市场情绪"
            badge={scans.length ? <Badge tone="emerald">{scans.length} 天</Badge> : null}
          />
          <AsyncBlock
            loading={scansQ.loading && !scansQ.data}
            error={scansQ.error}
            onRetry={scansQ.reload}
            loadingLabel="扫描快照加载中"
            empty={!scansQ.loading && !scansQ.error && scans.length === 0}
            emptyText="该策略暂无扫描快照"
            emptyHint="每日收盘跑批后会写入 strategy_scans"
          >
            <div className="mt-3 overflow-x-auto thin-scroll">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">日期</th>
                    <th className="pb-1.5 text-right font-medium">扫描</th>
                    <th className="pb-1.5 text-right font-medium">通过</th>
                    <th className="pb-1.5 text-right font-medium">情绪分</th>
                    <th className="pb-1.5 font-medium">心情</th>
                    <th className="hidden pb-1.5 font-medium sm:table-cell">过滤明细</th>
                  </tr>
                </thead>
                <tbody>
                  {scans.map((sc) => {
                    const stats = sc.filter_stats || sc.filterStats || null
                    const statChips =
                      stats && typeof stats === 'object' && !Array.isArray(stats)
                        ? Object.entries(stats).slice(0, 4)
                        : []
                    return (
                      <tr key={sc.id || sc.snapshot_date} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-1.5 tabular-nums text-slate-600 dark:text-slate-300">
                          {String(pick(sc, ['snapshot_date', 'trade_date', 'date']) || '—')}
                        </td>
                        <td className="py-1.5 text-right tabular-nums text-slate-500">
                          {fmtNum(n(sc.total_scanned), 0)}
                        </td>
                        <td className="py-1.5 text-right font-medium tabular-nums text-rose-600 dark:text-rose-400">
                          {fmtNum(n(sc.total_passed), 0)}
                        </td>
                        <td className="py-1.5 text-right tabular-nums text-slate-600 dark:text-slate-300">
                          {n(sc.sentiment_score) === null ? '—' : fmtNum(sc.sentiment_score, 0)}
                        </td>
                        <td className="py-1.5 text-slate-500">{sc.mood || '—'}</td>
                        <td className="hidden py-1.5 sm:table-cell">
                          <div className="flex flex-wrap gap-1">
                            {statChips.length
                              ? statChips.map(([k, v]) => (
                                  <span
                                    key={k}
                                    className="rounded border border-slate-200 bg-slate-50 px-1 text-[10px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400"
                                  >
                                    {k}: {typeof v === 'number' ? fmtNum(v, 0) : String(v)}
                                  </span>
                                ))
                              : null}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </AsyncBlock>
        </Card>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- 页面 */

/* --------------------------------------------------------------- 智能深度筛选 */

const SMART_PRESETS = [
  { value: 'winrate', label: '胜率优先' },
  { value: 'smart', label: '综合智能' },
  { value: 'trend', label: '趋势强势' },
  { value: 'growth', label: '成长质量' },
  { value: 'value', label: '低估值' },
  { value: 'capital', label: '资金驱动' },
  { value: 'breakout', label: '突破新高' },
  { value: 'reversal', label: '超跌反弹' },
  { value: 'leader', label: '龙头强势' },
  { value: 'quality', label: '高ROE' },
  { value: 'dividend', label: '蓝筹低波' },
]

const SMART_BOARDS = [
  { value: '', label: '全部' },
  { value: 'A主板', label: '主板' },
  { value: '创业板', label: '创业板' },
  { value: '科创板', label: '科创板' },
  { value: '北交所', label: '北交所' },
]

function FactorBar({ label, value }) {
  const v = n(value)
  return (
    <span className="inline-flex items-center gap-1" title={`${label} ${v ?? '—'}`}>
      <span className="text-[10px] text-slate-400">{label}</span>
      <span className="h-1.5 w-10 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <span className="block h-full rounded-full bg-brand-500" style={{ width: `${Math.max(0, Math.min(100, v || 0))}%` }} />
      </span>
      <span className="w-6 text-right text-[9px] tabular-nums text-slate-400">{v === null ? '—' : Math.round(v)}</span>
    </span>
  )
}

function SmartScanPanel({ onOpen }) {
  const [preset, setPreset] = useState('winrate')
  const [board, setBoard] = useState('')
  const [minScore, setMinScore] = useState(60)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const run = async () => {
    setLoading(true)
    try {
      const res = await api.get('/strategies/smart-scan', {
        preset,
        board: board || undefined,
        min_score: minScore,
        limit: 50,
      })
      setData(res)
      if (res?.market_blocked) {
        toast.info('大盘广度不足，胜率优先策略暂不开仓', `当前广度 ${fmtNum((res.market_breadth || 0) * 100, 1)}%，低于 55% 阈值`)
      } else if (!res?.hits?.length) {
        toast.info('没有达到门槛的标的', '可下调最低评分或切换预设')
      }
    } catch (e) {
      toast.error('智能扫描失败', e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <SectionHeader
        title="智能深度筛选"
        subtitle="全市场 5,000+ 标的 · 趋势/动量/量能/资金/质量/估值/风险 七因子打分，命中后按综合分排序"
        badge={data ? <Badge tone="blue">扫描 {fmtNum(data.total_scanned, 0)} 只 / {data.elapsed_ms}ms</Badge> : <Badge tone="amber">AI 多因子</Badge>}
      />
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="mb-1 block text-[11px] text-slate-400">筛选预设</span>
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            {SMART_PRESETS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
          </select>
        </label>
        <div>
          <span className="mb-1 block text-[11px] text-slate-400">板块</span>
          <Tabs tabs={SMART_BOARDS} value={board} onChange={setBoard} />
        </div>
        <label className="block">
          <span className="mb-1 block text-[11px] text-slate-400">最低评分</span>
          <input
            type="number"
            min="0"
            max="100"
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-20 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs tabular-nums dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          />
        </label>
        <Button size="sm" loading={loading} onClick={run}>开始全市场扫描</Button>
        <span className="text-[11px] text-slate-400">扫描口径：最新交易日收盘 + 最新财报</span>
      </div>

      {data?.market_blocked ? (
        <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          大盘广度 {fmtNum((data.market_breadth || 0) * 100, 1)}%，情绪广度 {fmtNum((data.sentiment_breadth || 0) * 100, 1)}%（20 日均值 {fmtNum((data.sentiment_breadth_ma20 || 0) * 100, 1)}%）；
          {data.market_breadth < 0.55 ? ' 大盘广度低于 55%，' : ' 情绪广度未较 20 日均值改善 5 个百分点，'}
          胜率优先策略暂停开仓，避免逆势交易。
        </div>
      ) : null}

      {data?.hits?.length ? (
        <div className="mt-4 max-h-[520px] overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
              <tr>
                <th className="px-2 py-2">#</th>
                <th className="px-2 py-2">股票</th>
                <th className="px-2 py-2 text-right">现价</th>
                <th className="px-2 py-2 text-right">今日</th>
                <th className="px-2 py-2 text-right">综合分</th>
                <th className="px-2 py-2">因子</th>
                <th className="px-2 py-2">入选理由</th>
              </tr>
            </thead>
            <tbody>
              {data.hits.map((h) => (
                <tr
                  key={h.ts_code}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                  onClick={() => onOpen(String(h.ts_code).split('.')[0])}
                >
                  <td className="px-2 py-1.5 text-slate-400">{h.rank}</td>
                  <td className="px-2 py-1.5">
                    <span className="font-medium text-slate-700 dark:text-slate-200">{h.name}</span>
                    <span className="ml-1 font-mono text-[10px] text-slate-400">{h.ts_code}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-200">{fmtNum(h.price, 2)}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums ${pctTone(h.pct_chg)}`}>{fmtPct(h.pct_chg)}</td>
                  <td className="px-2 py-1.5 text-right">
                    <span className="rounded-md bg-brand-50 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">
                      {fmtNum(h.score, 1)}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex flex-wrap gap-x-2 gap-y-1">
                      <FactorBar label="趋" value={h.factors?.trend} />
                      <FactorBar label="资" value={h.factors?.capital} />
                      <FactorBar label="质" value={h.factors?.quality} />
                      <FactorBar label="估" value={h.factors?.valuation} />
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-[11px] text-slate-500 dark:text-slate-400">{(h.reasons || []).join(' · ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="mt-2 text-[10px] text-slate-400">
        评分是本地量化模型的可解释结果，不是收益承诺；点击行进入个股 AI 量价推演。
      </p>
    </Card>
  )
}

export default function StrategyPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const listQ = useApi(() => api.get('/strategies'), [])
  const statsQ = useApi(() => api.get('/strategies/run-stats'), [])
  const [runCode, setRunCode] = useState(null)
  const [toggleCode, setToggleCode] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [showCreate, setShowCreate] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    name: '',
    code: '',
    family: 'custom',
    base_code: 'trend_pullback',
    description: '',
    prompt: '',
  })

  const items = useMemo(() => asItems(listQ.data), [listQ.data])
  const stats = useMemo(() => normalizeStats(statsQ.data), [statsQ.data])

  const refreshAll = useCallback(() => {
    listQ.reload()
    statsQ.reload()
    setReloadKey((k) => k + 1)
  }, [listQ, statsQ])

  /** 立即运行：loading → toast（扫描/通过/耗时）→ 重载列表与选中明细 */
  const handleRun = async (s) => {
    setRunCode(s.code)
    try {
      const res = (await api.post(`/strategies/${s.code}/run`)) || {}
      toast.success(
        `「${s.name}」运行完成`,
        `扫描 ${fmtNum(n(res.total_scanned), 0)} 只 · 通过 ${fmtNum(n(res.total_passed), 0)} 只 · 耗时 ${sec(res.elapsed_ms)}`,
      )
      if (!id) navigate(`/strategy/${s.code}`)
      refreshAll()
    } catch (e) {
      // 后端单元尚未上线时 404/500 也要给出可读反馈，不能静默
      toast.error('运行失败', e && e.message ? e.message : '请稍后重试')
    } finally {
      setRunCode(null)
    }
  }

  /** 启停开关：PATCH 后无论成败都重载，避免开关状态与后端不一致 */
  const handleToggle = async (s) => {
    setToggleCode(s.code)
    try {
      await api.patch(`/strategies/${s.code}`, { enabled: !s.enabled })
      toast.success(s.enabled ? `已停用「${s.name}」` : `已启用「${s.name}」`)
      refreshAll()
    } catch (e) {
      toast.error('启停失败', e && e.message ? e.message : '请稍后重试')
      refreshAll()
    } finally {
      setToggleCode(null)
    }
  }

  /** 规则式「AI 生成」：从自然语言里抽关键字匹配内置扫描器，不假装是外部大模型 */
  const aiFill = () => {
    const p = (form.prompt || '').trim()
    const pickBase = () => {
      if (/涨停|封板|连板/.test(p)) return 'limit_up_breakout'
      if (/回踩|低吸|趋势/.test(p)) return 'trend_pullback'
      if (/放量|量能|异动/.test(p)) return 'volume_surge'
      if (/换手|活跃/.test(p)) return 'high_turnover'
      if (/龙头|板块/.test(p)) return 'board_leader'
      if (/北向|外资/.test(p)) return 'northbound_inflow'
      return 'trend_pullback'
    }
    const base = pickBase()
    const tpl = items.find((x) => strategyOf(x).code === base)
    const family = tpl ? strategyOf(tpl).family : 'custom'
    const code = `ai_${Date.now().toString(36)}`
    setForm((f) => ({
      ...f,
      base_code: base,
      family,
      code,
      name: f.name || `AI ${p.slice(0, 12) || '自定义策略'}`,
      description: f.description || p || '由策略生成器按关键字匹配内置扫描器',
    }))
    toast.success('已生成策略草案', '确认参数后点击「创建策略」')
  }

  const createStrategy = async (e) => {
    e.preventDefault()
    if (!form.name.trim() || !form.code.trim()) {
      toast.error('名称和 code 必填')
      return
    }
    setSaving(true)
    try {
      const tpl = items.find((x) => strategyOf(x).code === form.base_code)
      const baseParams = (tpl && tpl.params) || {}
      await api.post('/strategies', {
        code: form.code.trim(),
        name: form.name.trim(),
        family: form.family || 'custom',
        description: form.description || '',
        params: { ...baseParams, base_code: form.base_code },
      })
      toast.success('策略已创建', '已绑定内置扫描器，可直接运行')
      setShowCreate(false)
      setForm({ name: '', code: '', family: 'custom', base_code: 'trend_pullback', description: '', prompt: '' })
      refreshAll()
    } catch (err) {
      toast.error('创建失败', err.message)
    } finally {
      setSaving(false)
    }
  }

  const tiles = [
    { label: '策略总数', value: fmtNum(stats.total_strategies ?? items.length, 0), sub: '内置可跑策略' },
    { label: '近 30 天运行次数', value: fmtNum(n(stats.runs_30d), 0), sub: '含手动跑批' },
    { label: '近 30 天命中数', value: fmtNum(n(stats.total_hits_30d), 0), sub: '累计入选个股' },
    { label: '平均耗时', value: sec(stats.avg_elapsed_ms), sub: '单次全市场扫描' },
    { label: '最近运行时间', value: stats.last_run_at ? fmtDateTime(stats.last_run_at) : '—', sub: '全策略最新' },
  ]

  return (
    <PageShell
      title="选股策略"
      subtitle="纯 SQL 全市场扫描 · 命中明细与扫描快照可回溯"
      actions={
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => setShowCreate(true)}>
            创建策略 / AI 生成
          </Button>
          <Button variant="secondary" size="sm" onClick={refreshAll}>
            刷新数据
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <SmartScanPanel onOpen={(code) => navigate(`/stock-analysis/${code}`)} />

        <AsyncBlock
          loading={statsQ.loading && !statsQ.data}
          error={statsQ.error}
          onRetry={statsQ.reload}
          loadingLabel="运行统计加载中"
        >
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            {tiles.map((t) => (
              <StatTile key={t.label} label={t.label} value={t.value} sub={t.sub} />
            ))}
          </div>
        </AsyncBlock>

        <AsyncBlock
          loading={listQ.loading && !listQ.data}
          error={listQ.error}
          onRetry={listQ.reload}
          loadingLabel="策略池加载中"
          empty={!listQ.loading && !listQ.error && items.length === 0}
          emptyText="策略池为空"
          emptyHint="后端内置策略上线后会自动出现在这里"
        >
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {items.map((raw, i) => {
              const s = strategyOf(raw)
              return (
                <StrategyCard
                  key={s.code || i}
                  index={i}
                  item={raw}
                  active={id === s.code}
                  running={runCode === s.code}
                  toggling={toggleCode === s.code}
                  onRun={handleRun}
                  onToggle={handleToggle}
                  onOpen={(st) => navigate(id === st.code ? '/strategy' : `/strategy/${st.code}`)}
                />
              )
            })}
          </div>
        </AsyncBlock>

        {id ? <StrategyDetail code={id} reloadKey={reloadKey} /> : null}

        {showCreate ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
            <Card className="w-full max-w-2xl">
              <SectionHeader
                title="创建策略"
                subtitle="选择内置扫描器作为模板；「AI 生成」只做关键字到模板的规则映射，结果可编辑"
                action={<Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>关闭</Button>}
              />
              <form onSubmit={createStrategy} className="mt-3 space-y-3">
                <label className="block">
                  <span className="mb-1 block text-[11px] text-slate-500">描述你的策略（AI 生成用）</span>
                  <div className="flex gap-2">
                    <input
                      value={form.prompt}
                      onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
                      placeholder="例如：趋势回踩低吸，放量突破，连板龙头"
                      className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                    <Button type="button" variant="secondary" size="sm" onClick={aiFill}>AI 生成</Button>
                  </div>
                </label>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500">策略名称</span>
                    <input
                      value={form.name}
                      onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500">策略 code（小写蛇形）</span>
                    <input
                      value={form.code}
                      onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                      placeholder="my_strategy"
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 font-mono text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500">扫描器模板</span>
                    <select
                      value={form.base_code}
                      onChange={(e) => setForm((f) => ({ ...f, base_code: e.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    >
                      {items.filter((x) => strategyOf(x).bestHitRate !== undefined).map((x) => {
                        const s = strategyOf(x)
                        return <option key={s.code} value={s.code}>{s.name}（{s.code}）</option>
                      })}
                    </select>
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500">说明</span>
                    <input
                      value={form.description}
                      onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                  </label>
                </div>
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setShowCreate(false)}>取消</Button>
                  <Button type="submit" loading={saving}>创建策略</Button>
                </div>
              </form>
            </Card>
          </div>
        ) : null}
      </div>
    </PageShell>
  )
}
