/**
 * /watchlist —— AI 持仓管家（自选）。
 *
 * 信息结构照原站：标题「AI 持仓管家 (已选 x/y 只)」→ 添加股票 / 清空 →
 * 破位邮件提醒开关（含原站说明文案）→ 行情更新状态行 → 行情表 → 共 n 条。
 *
 * 为什么这样写：
 * 1. 行情是本页唯一高频数据，单独 45 秒轮询且卸载即清理；分组与条目只在
 *    增删、切组时刷新，省掉无谓请求。
 * 2. 「添加股票」走 300ms 防抖搜索：停顿才发请求、点击即加入，加入成功后
 *    同时刷新条目与行情，用户不会看到「已加入但没价格」的中间态。
 * 3. 后端 B1 接口由并行单元实现、可能尚未上线：任何 404/500 都不白屏——
 *    分组拿不到给整页错误态可重试；只有行情失败时降级为横幅，列表照常可删。
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  ErrorState,
  PageShell,
  Pagination,
  SectionHeader,
  Select,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDateTime, fmtNum, fmtPct, marketTag, pctBg, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 20
const POLL_MS = 45_000

/* ------------------------------------------------------------------ 小工具 */

/** 失败文案：优先后端业务 reason，其次 detail/message，最后兜底。 */
function errText(e) {
  const p = e && e.payload
  const r = p && (p.reason || p.detail || p.message || p.error)
  if (typeof r === 'string' && r) return r
  return (e && e.message) || '操作失败，请稍后重试'
}

/** 契约是裸业务体，历史样例带 {code,data} 外壳，两者都兼容。 */
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

function totalOf(res, fallback) {
  const body = unwrap(res)
  return body && typeof body.total === 'number' ? body.total : fallback
}

/** 涨跌额：A 股看盘习惯带符号。 */
function fmtDelta(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  return `${Number(v) > 0 ? '+' : ''}${fmtNum(v, 2)}`
}

/* -------------------------------------------------------------- 局部组件 */

/** 开关：破位邮件提醒等二态设置。 */
function Toggle({ checked, busy, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked ? 'true' : 'false'}
      aria-label={label}
      disabled={busy}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
        checked ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600'
      }`}
    >
      <span
        className={`inline-block size-4 rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  )
}

/**
 * 代码/名称搜索：输入 → 300ms 防抖 → 搜索接口 → 点击结果回调。
 * 契约写的是 keyword，已上线实现用 q，两个参数一起传（FastAPI 忽略多余参数）。
 */
function SymbolSearch({ onPick, placeholder = '输入代码或名称，如 600519 / 贵州茅台', busyCode = null, pickLabel = '加入' }) {
  const [kw, setKw] = useState('')
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const seq = useRef(0)
  const wrapRef = useRef(null)

  // 点击外部收起结果面板，避免遮挡下方表格
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
      seq.current += 1 // 作废在途请求，防止清空输入后旧结果回填
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
  const showPanel = open && !!key

  return (
    <div ref={wrapRef} className="relative">
      <input
        value={kw}
        onChange={(e) => setKw(e.target.value)}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
      />
      {showPanel ? (
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
              const busy = busyCode === code
              return (
                <li key={code}>
                  <button
                    type="button"
                    disabled={busy}
                    // mousedown 时不让输入框失焦，否则 click 会被吞掉
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => onPick({ ts_code: code, name })}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition hover:bg-slate-50 disabled:opacity-60 dark:hover:bg-slate-800/70"
                  >
                    <Badge tone="blue">{marketTag(code)}</Badge>
                    <span className="font-medium text-slate-700 dark:text-slate-200">{name}</span>
                    <span className="tabular-nums text-[11px] text-slate-400">{code}</span>
                    {r.is_st ? <Badge tone="amber">ST</Badge> : null}
                    <span className="ml-auto text-[11px] font-medium text-brand-600 dark:text-brand-400">
                      {busy ? '处理中…' : pickLabel}
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

/* ------------------------------------------------------------------ 页面 */

export default function WatchlistPage() {
  const groupsApi = useApi(() => api.get('/watchlists'), [])
  const defaultApi = useApi(() => api.get('/watchlists/default'), [])

  const [gid, setGid] = useState('')
  const [page, setPage] = useState(1)
  const [showAdd, setShowAdd] = useState(false)
  const [addingCode, setAddingCode] = useState(null)
  const [removingCode, setRemovingCode] = useState(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [settingsBusy, setSettingsBusy] = useState(false)

  const groups = rowsOf(groupsApi.data)
  const def = unwrap(defaultApi.data) || {}
  // 选中分组：优先用户选择，其次分组列表首项，最后兜底「默认分组」接口
  const effective = useMemo(() => {
    if (gid && groups.some((g) => g.id === gid)) return gid
    return groups[0] ? groups[0].id : def.id || ''
  }, [gid, groups, def.id])

  const itemsApi = useApi(
    () => (effective ? api.get(`/watchlists/${effective}/items`) : Promise.resolve(null)),
    [effective],
  )
  const quotesApi = useApi(
    () =>
      effective
        ? api.get(`/watchlists/${effective}/quotes`, { page, size: PAGE_SIZE })
        : Promise.resolve(null),
    [effective, page],
  )
  const settingsApi = useApi(() => api.get('/watchlists/holding-alert-settings'), [])

  // 行情轮询：仅在页面可见时刷新，卸载即清理
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      quotesApi.reload()
      itemsApi.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [quotesApi.reload, itemsApi.reload])

  const itemRows = rowsOf(itemsApi.data)
  const quoteBody = unwrap(quotesApi.data) || {}
  const quoteRows = Array.isArray(quoteBody.items) ? quoteBody.items : []
  const meta = quoteBody.meta || {}

  // 条目提供 id（删除要靠它），行情提供价格；两者按 ts_code 合并，
  // 任一接口缺失都能降级出一张可读的表。
  const rows = useMemo(() => {
    const byCode = new Map(quoteRows.map((q) => [q.ts_code, q]))
    if (itemRows.length) {
      return itemRows.map((i) => ({ ...byCode.get(i.ts_code), ...i, itemId: i.id }))
    }
    return quoteRows.map((q) => ({ ...q, itemId: q.item_id ?? q.id }))
  }, [itemRows, quoteRows])

  const selCount = itemRows.length || rows.length
  const totalCount = totalOf(quotesApi.data, selCount)
  const pages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
  const groupName = (groups.find((g) => g.id === effective) || {}).name || def.name || '默认分组'

  const refreshed =
    typeof meta.refreshed === 'number' ? meta.refreshed : rows.filter((r) => r.price != null).length
  const missing =
    typeof meta.missing === 'number' ? meta.missing : rows.filter((r) => r.price == null).length

  const settings = unwrap(settingsApi.data) || {}
  const bootLoading = groupsApi.loading && defaultApi.loading && !groupsApi.data && !defaultApi.data
  const bootError =
    !bootLoading && !groupsApi.data && groupsApi.error && !defaultApi.data ? groupsApi.error : null

  /** 加入自选：成功后同时刷新条目与行情。 */
  const addItem = async ({ ts_code, name }) => {
    if (!effective) return
    setAddingCode(ts_code)
    try {
      await api.post(`/watchlists/${effective}/items`, { ts_code, stock_name: name })
      toast.success('已加入自选', `${name || ''} ${ts_code}`)
      await Promise.all([itemsApi.reload(), quotesApi.reload()])
    } catch (e) {
      toast.error('添加失败', errText(e))
    } finally {
      setAddingCode(null)
    }
  }

  /** 移除单只：失败保留行内 loading 结束态并可直接重试。 */
  const removeItem = async (row) => {
    if (!row.itemId) {
      toast.error('无法移除', '缺少条目标识，请刷新重试')
      return
    }
    const label = row.stock_name || row.name || row.ts_code
    setRemovingCode(row.ts_code)
    try {
      await api.del(`/watchlists/${effective}/items/${row.itemId}`)
      toast.success('已移除', `${label} ${row.ts_code}`)
      await Promise.all([itemsApi.reload(), quotesApi.reload()])
    } catch (e) {
      toast.error('移除失败', errText(e))
    } finally {
      setRemovingCode(null)
    }
  }

  /** 清空（二次确认）：逐条删除，成功/失败各报一次。 */
  const clearAll = async () => {
    const ids = rows.map((r) => r.itemId).filter(Boolean)
    if (!ids.length) {
      setConfirmClear(false)
      return
    }
    setClearing(true)
    let ok = 0
    let failed = 0
    for (const id of ids) {
      try {
        await api.del(`/watchlists/${effective}/items/${id}`)
        ok += 1
      } catch {
        failed += 1
      }
    }
    setClearing(false)
    setConfirmClear(false)
    if (failed === 0) toast.success('已清空', `共移除 ${ok} 只标的`)
    else if (ok === 0) toast.error('清空失败', `${failed} 只移除失败，请重试`)
    else toast.error('部分清空', `成功 ${ok} 只，失败 ${failed} 只，请重试`)
    await Promise.all([itemsApi.reload(), quotesApi.reload()])
  }

  /** 破位邮件提醒开关：PUT 后就地更新，避免整块重拉闪烁。 */
  const toggleSettings = async () => {
    const next = !settings.enabled
    setSettingsBusy(true)
    try {
      const res = await api.put('/watchlists/holding-alert-settings', { enabled: next })
      if (res) settingsApi.setData(res)
      else await settingsApi.reload()
      toast.success(next ? '已开启破位邮件提醒' : '已关闭破位邮件提醒')
    } catch (e) {
      toast.error('设置失败', errText(e))
      await settingsApi.reload()
    } finally {
      setSettingsBusy(false)
    }
  }

  if (bootLoading) {
    return (
      <PageShell title="AI 持仓管家" subtitle="自选分组加载中…">
        <Card>
          <AsyncBlock loading loadingLabel="自选分组加载中" />
        </Card>
      </PageShell>
    )
  }
  if (bootError) {
    return (
      <PageShell title="AI 持仓管家" subtitle="自选分组与实时行情">
        <Card>
          <AsyncBlock
            error={bootError}
            onRetry={() => {
              groupsApi.reload()
              defaultApi.reload()
            }}
          />
        </Card>
      </PageShell>
    )
  }

  return (
    <PageShell
      title={`AI 持仓管家 (已选 ${selCount}/${totalCount} 只)`}
      subtitle={`分组「${groupName}」· 行情 45 秒自动刷新 · 红涨绿跌`}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {groups.length > 1 ? (
            <Select
              value={effective}
              onChange={(v) => {
                setGid(v)
                setPage(1)
                setConfirmClear(false)
              }}
              options={groups.map((g) => ({ value: g.id, label: g.name }))}
              aria-label="切换自选分组"
            />
          ) : null}
          <Button onClick={() => setShowAdd((v) => !v)}>{showAdd ? '收起添加' : '添加股票'}</Button>
          <Button
            variant="secondary"
            disabled={!rows.length || clearing}
            onClick={() => setConfirmClear(true)}
          >
            清空
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        {showAdd ? (
          <Card>
            <SectionHeader
              title="添加股票"
              subtitle="按代码或名称搜索，点击结果即可加入当前分组；重复加入会幂等返回"
            />
            <div className="mt-3">
              <SymbolSearch onPick={addItem} busyCode={addingCode} pickLabel="加入" />
            </div>
          </Card>
        ) : null}

        {/* 破位邮件提醒：文案照抄原站 */}
        <Card>
          <AsyncBlock
            loading={settingsApi.loading && !settingsApi.data}
            error={settingsApi.error && !settingsApi.data}
            onRetry={settingsApi.reload}
            loadingLabel="提醒设置加载中"
          >
            <SectionHeader
              title="开启持仓破位邮件提醒"
              subtitle="开启后，当您的持仓股盘中跌破成本价的 5%，系统将自动发送邮件至您的复盘邮箱。"
              action={
                <Toggle
                  checked={!!settings.enabled}
                  busy={settingsBusy}
                  onChange={toggleSettings}
                  label="开启持仓破位邮件提醒"
                />
              }
            />
          </AsyncBlock>
        </Card>

        <Card>
          <SectionHeader
            title="自选行情"
            badge={
              <Badge tone={meta.degraded ? 'amber' : 'blue'}>
                {meta.source === 'db' ? '日线降级' : meta.source ? `来源 ${meta.source}` : '实时行情'}
              </Badge>
            }
            action={
              <Button size="sm" variant="secondary" loading={quotesApi.loading && !!quoteRows.length} onClick={() => quotesApi.reload()}>
                刷新行情
              </Button>
            }
          />
          <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
            行情更新：{fmtDateTime(quoteBody.as_of)} · 已刷新 {refreshed} 个标的 · 缺失 {missing} 个
          </p>
          {quotesApi.error ? (
            <div className="mt-2">
              <ErrorState
                error={quotesApi.error}
                onRetry={quotesApi.reload}
                text={`行情加载失败：${errText(quotesApi.error)}（自选列表仍可查看与删除）`}
              />
            </div>
          ) : null}

          <AsyncBlock
            loading={itemsApi.loading && rows.length === 0}
            error={itemsApi.error && rows.length === 0}
            onRetry={itemsApi.reload}
            empty={!itemsApi.loading && !itemsApi.error && rows.length === 0}
            emptyText="您还没有添加持仓股票"
            emptyHint="点击上方「添加股票」，按代码或名称搜索后加入。"
          >
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[720px] text-xs tabular-nums">
                <thead>
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">代码</th>
                    <th className="pb-1.5 font-medium">名称</th>
                    <th className="pb-1.5 text-right font-medium">现价</th>
                    <th className="pb-1.5 text-right font-medium">涨跌幅</th>
                    <th className="pb-1.5 text-right font-medium">涨跌额</th>
                    <th className="pb-1.5 text-right font-medium">成交额</th>
                    <th className="pb-1.5 text-right font-medium">换手</th>
                    <th className="pb-1.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const price = r.price ?? r.close ?? null
                    const chg = r.pct_chg
                    const delta =
                      r.change ?? (price != null && r.pre_close != null ? price - r.pre_close : null)
                    const name = r.stock_name || r.name || '—'
                    return (
                      <tr
                        key={r.itemId || r.ts_code}
                        className="border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                      >
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {r.ts_code}
                        </td>
                        <td className="py-2">
                          <span className="mr-1.5">
                            <Badge tone="blue">{marketTag(r.ts_code)}</Badge>
                          </span>
                          <span className="font-medium text-slate-700 dark:text-slate-200">{name}</span>
                        </td>
                        <td className={`py-2 text-right font-medium ${pctTone(chg)}`}>
                          {fmtNum(price, 2)}
                        </td>
                        <td className="py-2 text-right">
                          <span className={`rounded px-1.5 py-0.5 ${pctBg(chg)}`}>{fmtPct(chg)}</span>
                        </td>
                        <td className={`py-2 text-right ${pctTone(delta)}`}>{fmtDelta(delta)}</td>
                        <td className="py-2 text-right text-slate-500 dark:text-slate-400">
                          {fmtAmount(r.amount)}
                        </td>
                        <td className="py-2 text-right text-slate-500 dark:text-slate-400">
                          {r.turnover_rate == null ? '—' : `${fmtNum(r.turnover_rate, 2)}%`}
                        </td>
                        <td className="py-2 text-right">
                          <Button
                            size="sm"
                            variant="ghost"
                            loading={removingCode === r.ts_code}
                            onClick={() => removeItem(r)}
                          >
                            删除
                          </Button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {pages > 1 ? (
              <Pagination page={page} pageSize={PAGE_SIZE} total={totalCount} onChange={setPage} />
            ) : (
              <p className="pt-3 text-xs text-slate-500 dark:text-slate-400">共 {totalCount} 条</p>
            )}
          </AsyncBlock>
        </Card>

        {/* 清空二次确认：就地确认，不做弹窗，避免误触 */}
        {confirmClear ? (
          <Card className="border-amber-300 bg-amber-50/60 dark:border-amber-500/30 dark:bg-amber-500/10">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-amber-800 dark:text-amber-200">
                确认清空「{groupName}」中的 {rows.length} 只标的？移除后需重新搜索添加。
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={clearing} onClick={() => setConfirmClear(false)}>
                  取消
                </Button>
                <Button variant="danger" size="sm" loading={clearing} onClick={clearAll}>
                  确认清空
                </Button>
              </div>
            </div>
          </Card>
        ) : null}
      </div>
    </PageShell>
  )
}
