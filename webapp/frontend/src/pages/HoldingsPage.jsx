/**
 * /holdings —— 持仓账户。
 *
 * 结构：顶部 StatTile 汇总（总市值 / 总成本 / 总盈亏+盈亏比例 / 今日盈亏 / 胜率）
 * → 持仓表（可行内编辑、二次确认删除）→ 分页。添加持仓走「搜索选代码 + 数量 +
 * 成本价 + 路径 + 入场日 + 备注」表单。
 *
 * 为什么这样写：
 * 1. 盈亏是本页的核心语义，盈亏 / 盈亏比例一律 pctTone 着色（红涨绿跌），
 *    金额走 fmtAmount，行内数字统一 tabular-nums，方便竖向比对。
 * 2. 汇总与列表是两个接口，各自三态：汇总挂了不影响看列表，反之亦然。
 * 3. 所有写操作（新增 / 编辑 / 删除）都带行内 loading、成功 toast、
 *    失败展示后端原因并可直接重试，成功后 reload 汇总与列表。
 * 4. 行情随列表一起返回，45 秒轮询且卸载即清理；后端未上线时 404/500
 *    只落成错误态，绝不白屏。
 */
import React, { useEffect, useRef, useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  PageShell,
  Pagination,
  SectionHeader,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, marketTag, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 10
const POLL_MS = 45_000

function errText(e) {
  const p = e && e.payload
  const r = p && (p.reason || p.detail || p.message || p.error)
  if (typeof r === 'string' && r) return r
  return (e && e.message) || '操作失败，请稍后重试'
}

function unwrap(res) {
  if (res && typeof res === 'object' && !Array.isArray(res) && 'code' in res && 'data' in res) {
    return res.data
  }
  return res
}

function rowsOf(res) {
  const body = unwrap(res)
  if (Array.isArray(body)) return body
  return (body && body.items) || []
}

/** date 列直接存 YYYY-MM-DD 字符串，避免 new Date() 的时区回退一天。 */
function dateText(v) {
  if (!v) return '—'
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)) return v.slice(0, 10)
  return fmtDate(v)
}

/* -------------------------------------------------------------- 局部组件 */

/** 代码/名称搜索：300ms 防抖，点击结果回调选中的标的。 */
function SymbolSearch({ onPick, placeholder = '输入代码或名称搜索，如 600519 / 贵州茅台', busyCode = null, pickLabel = '选中' }) {
  const [kw, setKw] = useState('')
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const seq = useRef(0)
  const wrapRef = useRef(null)

  useEffect(() => {
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  useEffect(() => {
    const key = kw.trim()
    if (!key) {
      seq.current += 1
      setRows([])
      setErr(null)
      setLoading(false)
      return undefined
    }
    const timer = setTimeout(async () => {
      const id = ++seq.current
      setLoading(true)
      try {
        const res = await api.get('/market-data/securities/search', { q: key, keyword: key, limit: 8 })
        if (id === seq.current) {
          setRows(rowsOf(res))
          setErr(null)
        }
      } catch (e) {
        if (id === seq.current) {
          setRows([])
          setErr(e)
        }
      } finally {
        if (id === seq.current) setLoading(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [kw])

  const key = kw.trim()
  return (
    <div ref={wrapRef} className="relative">
      <input
        value={kw}
        onChange={(e) => setKw(e.target.value)}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
      />
      {open && key ? (
        <div className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900">
          <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-1.5 text-[11px] text-slate-400 dark:border-slate-800">
            <span>{loading ? '搜索中…' : `找到 ${rows.length} 个结果`}</span>
            <span>点击{pickLabel}</span>
          </div>
          {err ? <p className="px-3 py-2 text-[11px] text-rose-500">搜索失败：{errText(err)}</p> : null}
          {!err && !loading && rows.length === 0 ? (
            <p className="px-3 py-2 text-xs text-slate-400">没有匹配「{key}」的股票</p>
          ) : null}
          <ul className="max-h-64 overflow-y-auto thin-scroll">
            {rows.map((r) => {
              const code = r.ts_code
              const name = r.stock_name || r.name || ''
              return (
                <li key={code}>
                  <button
                    type="button"
                    disabled={busyCode === code}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => onPick({ ts_code: code, name })}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition hover:bg-slate-50 disabled:opacity-60 dark:hover:bg-slate-800/70"
                  >
                    <Badge tone="blue">{marketTag(code)}</Badge>
                    <span className="font-medium text-slate-700 dark:text-slate-200">{name}</span>
                    <span className="tabular-nums text-[11px] text-slate-400">{code}</span>
                    {r.is_st ? <Badge tone="amber">ST</Badge> : null}
                    <span className="ml-auto text-[11px] font-medium text-brand-600 dark:text-brand-400">
                      {busyCode === code ? '处理中…' : pickLabel}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

const EMPTY_FORM = { code: null, qty: '', cost: '', path: '', entry_date: '', notes: '' }

const INPUT_CLS =
  'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'

function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[10px] text-slate-400">{hint}</span> : null}
    </label>
  )
}

/* ------------------------------------------------------------------ 页面 */

export default function HoldingsPage() {
  const [page, setPage] = useState(1)
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(null) // {id, qty, cost, notes}
  const [editBusy, setEditBusy] = useState(false)
  const [confirmId, setConfirmId] = useState(null)
  const [delBusy, setDelBusy] = useState(null)

  const summaryApi = useApi(() => api.get('/holdings/summary'), [])
  const listApi = useApi(() => api.get('/holdings', { page, size: PAGE_SIZE }), [page])

  // 行情随列表返回，45 秒静默轮询；卸载时清理定时器
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      summaryApi.reload()
      listApi.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [summaryApi.reload, listApi.reload])

  const summary = unwrap(summaryApi.data) || {}
  const rows = rowsOf(listApi.data)
  const total = (unwrap(listApi.data) || {}).total || 0

  const win = Number(summary.win_count || 0)
  const lose = Number(summary.lose_count || 0)
  const winRate = win + lose > 0 ? `${((win / (win + lose)) * 100).toFixed(1)}%` : '—'

  const reloadAll = () => Promise.all([summaryApi.reload(), listApi.reload()])

  /** 新增持仓：先校验必填，再 POST，成功后重置表单并刷新汇总。 */
  const submitAdd = async () => {
    if (!form.code) {
      toast.error('请选择股票', '请先在上方搜索并选择一只股票')
      return
    }
    const qty = Number(form.qty)
    const cost = Number(form.cost)
    if (!(qty > 0)) {
      toast.error('请填写持仓量', '数量需大于 0 股')
      return
    }
    if (!(cost > 0)) {
      toast.error('请填写成本价', '成本价需大于 0')
      return
    }
    setSaving(true)
    try {
      await api.post('/holdings', {
        ts_code: form.code.ts_code,
        qty,
        cost,
        path: form.path || undefined,
        entry_date: form.entry_date || undefined,
        notes: form.notes || undefined,
      })
      toast.success('持仓已添加', `${form.code.name || ''} ${form.code.ts_code} · ${qty} 股`)
      setForm(EMPTY_FORM)
      setPage(1)
      await reloadAll()
    } catch (e) {
      toast.error('添加失败', errText(e))
    } finally {
      setSaving(false)
    }
  }

  /** 行内编辑保存（数量 / 成本 / 备注）。 */
  const submitEdit = async () => {
    const qty = Number(editing.qty)
    const cost = Number(editing.cost)
    if (!(qty > 0) || !(cost > 0)) {
      toast.error('请检查输入', '数量与成本价都需大于 0')
      return
    }
    setEditBusy(true)
    try {
      await api.patch(`/holdings/${editing.id}`, { qty, cost, notes: editing.notes || '' })
      toast.success('持仓已更新')
      setEditing(null)
      await reloadAll()
    } catch (e) {
      toast.error('保存失败', errText(e))
    } finally {
      setEditBusy(false)
    }
  }

  /** 删除：二次确认后执行，失败保留确认态可重试。 */
  const submitDel = async (row) => {
    setDelBusy(row.id)
    try {
      await api.del(`/holdings/${row.id}`)
      toast.success('持仓已删除', `${row.stock_name || row.ts_code} ${row.ts_code}`)
      setConfirmId(null)
      if (editing && editing.id === row.id) setEditing(null)
      await reloadAll()
    } catch (e) {
      toast.error('删除失败', errText(e))
    } finally {
      setDelBusy(null)
    }
  }

  return (
    <PageShell
      title="持仓账户"
      subtitle={`共 ${total} 笔持仓 · 行情 45 秒自动刷新 · 红涨绿跌`}
      actions={
        <Button onClick={() => setShowAdd((v) => !v)}>{showAdd ? '收起添加' : '添加持仓'}</Button>
      }
    >
      <div className="space-y-4">
        {/* 汇总磁贴 */}
        <Card>
          <AsyncBlock
            loading={summaryApi.loading && !summaryApi.data}
            error={summaryApi.error && !summaryApi.data}
            onRetry={summaryApi.reload}
            loadingLabel="账户汇总加载中"
          >
            <SectionHeader
              title="账户汇总"
              subtitle={`持仓 ${summary.items ?? rows.length} 笔 · 盈利 ${win} · 亏损 ${lose}`}
              badge={<Badge tone="blue">自动刷新</Badge>}
            />
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
              <StatTileLite label="总市值" value={fmtAmount(summary.total_value)} />
              <StatTileLite label="总成本" value={fmtAmount(summary.total_cost)} />
              <StatTileLite
                label="总盈亏"
                value={fmtAmount(summary.total_pnl)}
                tone={pctTone(summary.total_pnl)}
                sub={`盈亏比例 ${fmtPct(summary.pnl_pct)}`}
              />
              <StatTileLite
                label="今日盈亏"
                value={fmtAmount(summary.today_pnl)}
                tone={pctTone(summary.today_pnl)}
              />
              <StatTileLite label="胜率" value={winRate} sub={`盈利 ${win} · 亏损 ${lose}`} />
            </div>
          </AsyncBlock>
        </Card>

        {/* 添加持仓 */}
        {showAdd ? (
          <Card>
            <SectionHeader title="添加持仓" subtitle="搜索选代码 → 填数量与成本 → 提交后立即计入汇总" />
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div className="space-y-3">
                <Field label="股票">
                  <SymbolSearch
                    busyCode={saving ? form.code && form.code.ts_code : null}
                    onPick={(s) => setForm((f) => ({ ...f, code: s }))}
                  />
                </Field>
                {form.code ? (
                  <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-800/60">
                    <Badge tone="blue">{marketTag(form.code.ts_code)}</Badge>
                    <span className="font-medium text-slate-700 dark:text-slate-200">
                      {form.code.name}
                    </span>
                    <span className="tabular-nums text-slate-400">{form.code.ts_code}</span>
                    <button
                      type="button"
                      onClick={() => setForm((f) => ({ ...f, code: null }))}
                      className="ml-auto text-slate-400 hover:text-rose-500"
                      aria-label="取消选择"
                    >
                      ✕
                    </button>
                  </div>
                ) : null}
                <div className="grid grid-cols-2 gap-3">
                  <Field label="持仓量（股）">
                    <input
                      type="number"
                      min="1"
                      step="100"
                      value={form.qty}
                      onChange={(e) => setForm((f) => ({ ...f, qty: e.target.value }))}
                      placeholder="如 1000"
                      className={INPUT_CLS}
                    />
                  </Field>
                  <Field label="成本价（元）">
                    <input
                      type="number"
                      min="0"
                      step="0.001"
                      value={form.cost}
                      onChange={(e) => setForm((f) => ({ ...f, cost: e.target.value }))}
                      placeholder="如 1680.00"
                      className={INPUT_CLS}
                    />
                  </Field>
                </div>
              </div>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <Field label="路径（可选）">
                    <input
                      value={form.path}
                      onChange={(e) => setForm((f) => ({ ...f, path: e.target.value }))}
                      placeholder="如 主力账户 / 定投"
                      className={INPUT_CLS}
                    />
                  </Field>
                  <Field label="入场日期（可选）">
                    <input
                      type="date"
                      value={form.entry_date}
                      onChange={(e) => setForm((f) => ({ ...f, entry_date: e.target.value }))}
                      className={INPUT_CLS}
                    />
                  </Field>
                </div>
                <Field label="备注（可选）">
                  <input
                    value={form.notes}
                    onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                    placeholder="如 回踩 20 日线建仓"
                    className={INPUT_CLS}
                  />
                </Field>
                <div className="flex justify-end gap-2">
                  <Button variant="secondary" disabled={saving} onClick={() => setForm(EMPTY_FORM)}>
                    清空表单
                  </Button>
                  <Button loading={saving} onClick={submitAdd}>
                    提交持仓
                  </Button>
                </div>
              </div>
            </div>
          </Card>
        ) : null}

        {/* 持仓明细 */}
        <Card>
          <SectionHeader
            title="持仓明细"
            subtitle="点击「编辑」可就地修改数量、成本与备注"
            action={
              <Button size="sm" variant="secondary" loading={listApi.loading && rows.length > 0} onClick={() => reloadAll()}>
                刷新
              </Button>
            }
          />
          <AsyncBlock
            loading={listApi.loading && rows.length === 0}
            error={listApi.error && rows.length === 0}
            onRetry={listApi.reload}
            empty={!listApi.loading && !listApi.error && rows.length === 0}
            emptyText="暂无持仓记录"
            emptyHint="点击上方「添加持仓」，搜索股票后录入第一笔持仓。"
          >
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[960px] text-xs tabular-nums">
                <thead>
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">代码</th>
                    <th className="pb-1.5 font-medium">名称</th>
                    <th className="pb-1.5 text-right font-medium">持仓量</th>
                    <th className="pb-1.5 text-right font-medium">成本价</th>
                    <th className="pb-1.5 text-right font-medium">现价</th>
                    <th className="pb-1.5 text-right font-medium">市值</th>
                    <th className="pb-1.5 text-right font-medium">盈亏</th>
                    <th className="pb-1.5 text-right font-medium">盈亏比例</th>
                    <th className="pb-1.5 text-right font-medium">入场日</th>
                    <th className="pb-1.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((h) => {
                    const qty = h.qty ?? h.quantity ?? null
                    const cost = h.cost ?? h.cost_price ?? null
                    const price = h.price ?? h.last_price ?? null
                    const mv =
                      h.market_value ?? (price != null && qty != null ? qty * price : null)
                    const pnl =
                      h.pnl ??
                      (mv != null && cost != null && qty != null ? mv - qty * cost : null)
                    const pnlPct =
                      h.pnl_pct ??
                      (pnl != null && cost > 0 && qty > 0 ? (pnl / (cost * qty)) * 100 : null)
                    const isEditing = editing && editing.id === h.id
                    return [
                      <tr
                        key={h.id}
                        className="border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                      >
                        <td className="py-2 text-slate-500 dark:text-slate-400">{h.ts_code}</td>
                        <td className="py-2">
                          <span className="mr-1.5">
                            <Badge tone="blue">{marketTag(h.ts_code)}</Badge>
                          </span>
                          <span className="font-medium text-slate-700 dark:text-slate-200">
                            {h.stock_name || h.name || '—'}
                          </span>
                        </td>
                        <td className="py-2 text-right text-slate-600 dark:text-slate-300">
                          {fmtNum(qty, 0)}
                        </td>
                        <td className="py-2 text-right text-slate-600 dark:text-slate-300">
                          {fmtNum(cost, 3)}
                        </td>
                        <td className={`py-2 text-right font-medium ${pctTone(h.pct_chg)}`}>
                          {fmtNum(price, 2)}
                        </td>
                        <td className="py-2 text-right text-slate-600 dark:text-slate-300">
                          {fmtAmount(mv)}
                        </td>
                        <td className={`py-2 text-right font-medium ${pctTone(pnl)}`}>
                          {fmtAmount(pnl)}
                        </td>
                        <td className={`py-2 text-right font-medium ${pctTone(pnlPct)}`}>
                          {fmtPct(pnlPct)}
                        </td>
                        <td className="py-2 text-right text-slate-500 dark:text-slate-400">
                          {dateText(h.entry_date)}
                        </td>
                        <td className="py-2 text-right">
                          <div className="inline-flex gap-1">
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={editBusy}
                              onClick={() =>
                                setEditing(
                                  isEditing
                                    ? null
                                    : {
                                        id: h.id,
                                        qty: qty ?? '',
                                        cost: cost ?? '',
                                        notes: h.notes || '',
                                      },
                                )
                              }
                            >
                              {isEditing ? '取消' : '编辑'}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={confirmId !== null && confirmId !== h.id}
                              onClick={() => setConfirmId(confirmId === h.id ? null : h.id)}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>,
                      isEditing ? (
                        <tr
                          key={`${h.id}-edit`}
                          className="border-t border-brand-100 bg-brand-50/40 dark:border-brand-500/20 dark:bg-brand-500/5"
                        >
                          <td colSpan={10} className="py-3">
                            <div className="flex flex-wrap items-end gap-3">
                              <div className="w-32">
                                <Field label="数量（股）">
                                  <input
                                    type="number"
                                    min="1"
                                    step="100"
                                    value={editing.qty}
                                    onChange={(e) =>
                                      setEditing((s) => ({ ...s, qty: e.target.value }))
                                    }
                                    className={INPUT_CLS}
                                  />
                                </Field>
                              </div>
                              <div className="w-32">
                                <Field label="成本价（元）">
                                  <input
                                    type="number"
                                    min="0"
                                    step="0.001"
                                    value={editing.cost}
                                    onChange={(e) =>
                                      setEditing((s) => ({ ...s, cost: e.target.value }))
                                    }
                                    className={INPUT_CLS}
                                  />
                                </Field>
                              </div>
                              <div className="min-w-52 flex-1">
                                <Field label="备注">
                                  <input
                                    value={editing.notes}
                                    onChange={(e) =>
                                      setEditing((s) => ({ ...s, notes: e.target.value }))
                                    }
                                    className={INPUT_CLS}
                                  />
                                </Field>
                              </div>
                              <Button size="sm" loading={editBusy} onClick={submitEdit}>
                                保存修改
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ) : null,
                      confirmId === h.id ? (
                        <tr
                          key={`${h.id}-confirm`}
                          className="border-t border-amber-200 bg-amber-50/70 dark:border-amber-500/30 dark:bg-amber-500/10"
                        >
                          <td colSpan={10} className="py-2.5">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p className="text-xs text-amber-800 dark:text-amber-200">
                                确认删除 {h.stock_name || h.ts_code}（{h.ts_code}）的持仓记录？
                                删除后汇总同步减少，不可恢复。
                              </p>
                              <div className="flex gap-2">
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  disabled={delBusy === h.id}
                                  onClick={() => setConfirmId(null)}
                                >
                                  取消
                                </Button>
                                <Button
                                  size="sm"
                                  variant="danger"
                                  loading={delBusy === h.id}
                                  onClick={() => submitDel(h)}
                                >
                                  确认删除
                                </Button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null,
                    ]
                  })}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
          </AsyncBlock>
        </Card>
      </div>
    </PageShell>
  )
}

/** 磁贴：复用 StatTile 的视觉，但要支持 tone / sub 两个扩展位。 */
function StatTileLite({ label, value, sub = null, tone = null }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/50">
      <p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p>
      <p
        className={`mt-1 text-lg font-semibold tabular-nums ${
          tone || 'text-slate-900 dark:text-slate-100'
        }`}
      >
        {value}
      </p>
      {sub ? <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{sub}</p> : null}
    </div>
  )
}
