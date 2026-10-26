/**
 * 价格提醒（/holdings/alerts）：把「盯盘条件」变成可执行、可回溯的规则。
 *
 * 三块能力：
 *   1. 持仓破位提醒总开关 + 阈值（PUT /watchlists/holding-alert-settings）；
 *   2. 个股提醒规则 CRUD（/alerts），支持价格、涨跌幅、涨跌停、破成本、成交额；
 *   3. 最近触发记录（/alerts/triggered）——后端在每次列表请求时惰性评估一次。
 *
 * 后端没有推送通道，因此页面 60 秒轮询一次；触发记录按 last_triggered_at 倒序。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  EmptyState,
  PageShell,
  SectionHeader,
  Select,
  Tabs,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum, fmtPct, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const POLL_MS = 60_000

const ALERT_TYPES = [
  { value: 'price_above', label: '价格突破', unit: '元', direction: 'up' },
  { value: 'price_below', label: '价格跌破', unit: '元', direction: 'down' },
  { value: 'pct_chg_above', label: '涨幅超过', unit: '%', direction: 'up' },
  { value: 'pct_chg_below', label: '跌幅超过', unit: '%', direction: 'down' },
  { value: 'limit_up', label: '涨停', unit: '', direction: 'up' },
  { value: 'limit_down', label: '跌停', unit: '', direction: 'down' },
  { value: 'break_cost', label: '跌破成本价', unit: '%', direction: 'down' },
  { value: 'amount_above', label: '成交额超过', unit: '亿元', direction: 'up' },
]
const TYPE_MAP = Object.fromEntries(ALERT_TYPES.map((t) => [t.value, t]))
const FILTER_TABS = [{ value: '', label: '全部' }, ...ALERT_TYPES.map((t) => ({ value: t.value, label: t.label }))]

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function alertValue(a) {
  const th = num(a.threshold)
  if (th === null) return '—'
  const t = TYPE_MAP[a.alert_type]
  if (!t || !t.unit) return '—'
  return t.unit === '%' ? `${fmtNum(th, 2)}%` : t.unit === '亿元' ? `${fmtNum(th, 2)} 亿` : `${fmtNum(th, 2)} 元`
}

function AlertCard({ alert, busy, onToggle, onDelete }) {
  const t = TYPE_MAP[alert.alert_type] || { label: alert.alert_type, unit: '' }
  const triggered = !!alert.last_triggered_at
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">
            {alert.stock_name || alert.ts_code}
            <span className="ml-1.5 font-mono text-[10px] font-normal text-slate-400">{alert.ts_code}</span>
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge tone="blue">{t.label}</Badge>
            <Badge tone="slate">阈值 {alertValue(alert)}</Badge>
            {triggered ? (
              <Badge tone="amber">已触发 {alert.trigger_count || 1} 次</Badge>
            ) : (
              <Badge tone="emerald">监控中</Badge>
            )}
            {!alert.enabled ? <Badge tone="slate">已停用</Badge> : null}
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={!!alert.enabled}
          aria-label={alert.enabled ? '停用提醒' : '启用提醒'}
          disabled={busy}
          onClick={() => onToggle(alert)}
          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-60 ${
            alert.enabled ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600'
          }`}
        >
          <span
            className={`inline-block size-4 rounded-full bg-white shadow transition-transform ${
              alert.enabled ? 'translate-x-4' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>

      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-slate-400 dark:text-slate-500">
        <span className="min-w-0 truncate">
          {alert.last_message ? alert.last_message : `创建于 ${fmtDateTime(alert.created_at)}`}
        </span>
        <span className="flex shrink-0 gap-2">
          {alert.last_triggered_at ? <span>触发 {fmtDateTime(alert.last_triggered_at)}</span> : null}
          <button
            type="button"
            className="text-rose-500 hover:underline disabled:opacity-50"
            disabled={busy}
            onClick={() => onDelete(alert)}
          >
            删除
          </button>
        </span>
      </div>
    </li>
  )
}

function TriggeredPanel({ data }) {
  const items = data?.items || []
  return (
    <Card className="h-fit">
      <SectionHeader
        title="最近触发"
        subtitle="后端在列表请求时惰性评估一次实时价"
        badge={<Badge tone="amber">{data?.total ?? 0} 条</Badge>}
      />
      {items.length === 0 ? (
        <EmptyState text="还没有触发记录" hint="行情满足条件后，这里会出现触发时间与说明" />
      ) : (
        <ul className="mt-2 space-y-2">
          {items.map((a) => (
            <li key={a.id} className="rounded-lg border border-slate-100 p-2 text-xs dark:border-slate-800">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-slate-700 dark:text-slate-200">
                  {a.stock_name || a.ts_code}
                </span>
                <span className="shrink-0 text-[10px] text-slate-400">{fmtDateTime(a.last_triggered_at)}</span>
              </div>
              <p className="mt-1 text-[11px] leading-5 text-slate-500 dark:text-slate-400">
                {a.last_message || `${TYPE_MAP[a.alert_type]?.label || a.alert_type} 已触发`}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export default function HoldingsAlertsPage() {
  const [type, setType] = useState('')
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 20
  const [busyId, setBusyId] = useState(null)
  const [form, setForm] = useState({ ts_code: '', alert_type: 'price_above', threshold: '' })
  const [saving, setSaving] = useState(false)
  const [settings, setSettings] = useState({ enabled: false, threshold: 5 })
  const [settingsSaving, setSettingsSaving] = useState(false)

  const list = useApi(
    () => api.get('/alerts', { page, size: PAGE_SIZE, type: type || undefined }),
    [page, type],
  )
  const triggered = useApi(() => api.get('/alerts/triggered', { limit: 20 }), [])
  const settingsQ = useApi(() => api.get('/watchlists/holding-alert-settings'), [])

  useEffect(() => {
    if (settingsQ.data) {
      setSettings({
        enabled: !!settingsQ.data.enabled,
        threshold: num(settingsQ.data.threshold) ?? 5,
      })
    }
  }, [settingsQ.data])

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      list.reload()
      triggered.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [list.reload, triggered.reload])

  const items = list.data?.items || []
  const selectedType = TYPE_MAP[form.alert_type] || ALERT_TYPES[0]
  const needThreshold = selectedType.unit !== ''

  async function saveSettings(next) {
    setSettingsSaving(true)
    const prev = settings
    setSettings(next)
    try {
      const res = await api.put('/watchlists/holding-alert-settings', next)
      setSettings({ enabled: !!res.enabled, threshold: num(res.threshold) ?? 5 })
      toast.success('已保存破位提醒设置')
    } catch (e) {
      setSettings(prev)
      toast.error('保存失败', e.message)
    } finally {
      setSettingsSaving(false)
    }
  }

  async function submit(e) {
    e.preventDefault()
    const code = form.ts_code.trim().toUpperCase()
    if (!code) {
      toast.error('请填写股票代码', '如 600519 或 600519.SH')
      return
    }
    let normalized = code
    if (!normalized.includes('.')) {
      normalized = `${normalized}.${normalized.startsWith('6') || normalized.startsWith('9') ? 'SH' : 'SZ'}`
    }
    const th = needThreshold ? num(form.threshold) : null
    if (needThreshold && (th === null || th <= 0)) {
      toast.error('请填写有效的阈值', `单位：${selectedType.unit}`)
      return
    }
    setSaving(true)
    try {
      const body = { ts_code: normalized, alert_type: form.alert_type }
      if (th !== null) body.threshold = th
      if (selectedType.direction) body.direction = selectedType.direction
      if (form.alert_type === 'amount_above' && th !== null) body.threshold = th * 1e8
      await api.post('/alerts', body)
      toast.success('提醒已创建', `${normalized} · ${selectedType.label}`)
      setForm((f) => ({ ...f, ts_code: '', threshold: '' }))
      list.reload()
      triggered.reload()
    } catch (e2) {
      toast.error('创建失败', e2.message)
    } finally {
      setSaving(false)
    }
  }

  async function toggle(alert) {
    setBusyId(alert.id)
    try {
      await api.patch(`/alerts/${alert.id}`, { enabled: !alert.enabled })
      toast.success(alert.enabled ? '已停用提醒' : '已启用提醒')
      list.reload()
    } catch (e) {
      toast.error('操作失败', e.message)
    } finally {
      setBusyId(null)
    }
  }

  async function remove(alert) {
    setBusyId(alert.id)
    try {
      await api.del(`/alerts/${alert.id}`)
      toast.success('已删除提醒')
      list.reload()
      triggered.reload()
    } catch (e) {
      toast.error('删除失败', e.message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <PageShell
      title="价格提醒"
      subtitle="个股价格 / 涨跌幅 / 涨跌停 / 破成本价 / 成交额，满足条件即记录触发"
      actions={<span className="text-[11px] text-slate-400">每 60 秒自动刷新</span>}
    >
      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <Card>
            <SectionHeader
              title="持仓破位提醒"
              subtitle="跌破持仓成本价的设定比例时触发（原站默认 5%）"
              action={
                <label className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                  开启
                  <button
                    type="button"
                    role="switch"
                    aria-checked={settings.enabled}
                    disabled={settingsSaving}
                    onClick={() => saveSettings({ ...settings, enabled: !settings.enabled })}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      settings.enabled ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600'
                    }`}
                  >
                    <span
                      className={`inline-block size-4 rounded-full bg-white shadow transition-transform ${
                        settings.enabled ? 'translate-x-4' : 'translate-x-0.5'
                      }`}
                    />
                  </button>
                </label>
              }
            />
            <div className="mt-2 flex flex-wrap items-end gap-2 text-xs text-slate-500 dark:text-slate-400">
              <label className="flex items-center gap-1.5">
                破位阈值
                <input
                  type="number"
                  min="0.5"
                  max="50"
                  step="0.5"
                  value={settings.threshold}
                  onChange={(e) => setSettings((s) => ({ ...s, threshold: e.target.value }))}
                  className="w-20 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs tabular-nums text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                />
                %
              </label>
              <Button size="sm" variant="secondary" loading={settingsSaving} onClick={() => saveSettings(settings)}>
                保存阈值
              </Button>
              <span className="text-[11px] text-slate-400">关闭时仍保留个股提醒规则</span>
            </div>
          </Card>

          <Card>
            <SectionHeader title="新建提醒" subtitle="代码支持 600519 / 600519.SH 两种写法" />
            <form onSubmit={submit} className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_120px_auto]">
              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">股票代码</span>
                <input
                  value={form.ts_code}
                  onChange={(e) => setForm((f) => ({ ...f, ts_code: e.target.value }))}
                  placeholder="600519"
                  className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">提醒类型</span>
                <Select
                  value={form.alert_type}
                  onChange={(v) => setForm((f) => ({ ...f, alert_type: v }))}
                  options={ALERT_TYPES.map((t) => ({ value: t.value, label: t.label }))}
                  className="w-full py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">
                  阈值{needThreshold ? `（${selectedType.unit}）` : '（无需）'}
                </span>
                <input
                  type="number"
                  step="0.01"
                  value={form.threshold}
                  disabled={!needThreshold}
                  onChange={(e) => setForm((f) => ({ ...f, threshold: e.target.value }))}
                  placeholder={needThreshold ? '如 10' : '自动'}
                  className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm tabular-nums text-slate-700 outline-none focus:border-brand-500 disabled:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:disabled:bg-slate-800/40"
                />
              </label>
              <div className="flex items-end">
                <Button type="submit" loading={saving} className="w-full sm:w-auto">
                  添加提醒
                </Button>
              </div>
            </form>
          </Card>

          <Card>
            <SectionHeader
              title="提醒规则"
              subtitle={`共 ${list.data?.total ?? 0} 条 · 触发后仍保持监控，可随时停用`}
              badge={<Badge tone="blue">{items.length} 条 / 本页</Badge>}
            />
            <div className="mt-3">
              <Tabs
                tabs={FILTER_TABS}
                value={type}
                onChange={(v) => {
                  setType(v)
                  setPage(1)
                }}
              />
            </div>
            {!list.data ? (
              <AsyncBlock loading={list.loading} error={list.error} onRetry={list.reload} loadingLabel="提醒列表加载中…" />
            ) : items.length === 0 ? (
              <EmptyState text="暂无提醒规则" hint="在上方选择类型与阈值，添加第一条价格提醒" />
            ) : (
              <>
                <ul className="mt-3 space-y-2">
                  {items.map((a) => (
                    <AlertCard
                      key={a.id}
                      alert={a}
                      busy={busyId === a.id}
                      onToggle={toggle}
                      onDelete={remove}
                    />
                  ))}
                </ul>
                {list.data.total > PAGE_SIZE ? (
                  <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
                    <span>共 {list.data.total} 条</span>
                    <div className="flex gap-2">
                      <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                        上一页
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={page * PAGE_SIZE >= list.data.total}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        下一页
                      </Button>
                    </div>
                  </div>
                ) : null}
              </>
            )}
          </Card>
        </div>

        <TriggeredPanel data={triggered.data} />
      </div>
    </PageShell>
  )
}
