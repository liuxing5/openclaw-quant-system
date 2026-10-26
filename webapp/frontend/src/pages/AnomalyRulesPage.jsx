/**
 * 异动提醒规则（/anomaly/rules）：规则的增删改查与启停。
 *
 * 为什么用卡片而不是表格：一条规则的关键信息是「参数怎么配」，键值 chips 比
 * 单元格更易读；启停、编辑、删除是高频动作，放在卡片底部一次可达。
 * 所有写操作统一走 loading → toast → reload 三段式，失败时表单/按钮保持可用以便重试。
 */
import { useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  EmptyState,
  PageShell,
  Pagination,
  SectionHeader,
  Select,
  StatTile,
  Tabs,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDateTime, fmtNum } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 20

const KIND_OPTIONS = [
  { value: 'rapid_rise', label: '急拉' },
  { value: 'volume_spike', label: '放量' },
  { value: 'limit_up', label: '涨停' },
  { value: 'limit_down', label: '跌停' },
  { value: 'turnover_high', label: '高换手' },
  { value: 'new_high', label: '新高' },
  { value: 'board_surge', label: '板块大涨' },
]
const KIND_TONE = {
  rapid_rise: 'rose',
  volume_spike: 'amber',
  limit_up: 'rose',
  limit_down: 'emerald',
  turnover_high: 'amber',
  new_high: 'blue',
  board_surge: 'rose',
}

const SCOPE_OPTIONS = [
  { value: 'symbol', label: '个股（可指定标的）' },
  { value: 'board', label: '板块' },
  { value: 'all_market', label: '全市场' },
]
const SCOPE_LABEL = { symbol: '个股', board: '板块', all_market: '全市场' }

/** 表单里可填的结构化参数：全部按数值上送，空值不写进 params */
const PARAM_FIELDS = [
  { key: 'pct_chg', label: '涨跌幅阈值（%）', placeholder: '如 5' },
  { key: 'volume_ratio', label: '量比阈值', placeholder: '如 2' },
  { key: 'turnover_rate', label: '换手率阈值（%）', placeholder: '如 15' },
  { key: 'amount', label: '成交额阈值（元）', placeholder: '如 300000000' },
  { key: 'window_min', label: '时间窗口（分钟）', placeholder: '如 3' },
]

/** params 键 -> 中文标签与单位，让规则卡片一眼能读懂触发条件 */
const PARAM_META = {
  pct_chg: { label: '涨跌幅', unit: '%', digits: 2 },
  volume_ratio: { label: '量比', unit: '×', digits: 2 },
  turnover_rate: { label: '换手率', unit: '%', digits: 1 },
  amount: { label: '成交额', isAmount: true },
  window_min: { label: '时间窗口', unit: ' 分钟', digits: 0 },
  threshold: { label: '阈值', unit: '', digits: 2 },
  days: { label: '统计周期', unit: ' 日', digits: 0 },
  top_n: { label: '前 N 名', unit: '', digits: 0 },
  ts_code: { label: '指定标的' },
}

const INPUT =
  'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'

const EMPTY_FORM = {
  name: '',
  kind: 'rapid_rise',
  scope: 'symbol',
  ts_code: '',
  params: { pct_chg: '', volume_ratio: '', turnover_rate: '', amount: '', window_min: '' },
}

function paramText(key, value) {
  const meta = PARAM_META[key]
  if (!meta) return `${key}：${String(value)}`
  if (meta.isAmount) return `${meta.label} ${fmtAmount(value)}`
  if (typeof value === 'number') return `${meta.label} ${value.toFixed(meta.digits ?? 2)}${meta.unit || ''}`
  return `${meta.label} ${value}${meta.unit || ''}`
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[10px] text-slate-400 dark:text-slate-500">{hint}</span> : null}
    </label>
  )
}

function RuleCard({ rule, busy, confirming, deleting, onToggle, onEdit, onConfirmDelete, onCancelDelete }) {
  const params = rule.params || {}
  const chips = Object.entries(params)
  const kindLabel = KIND_OPTIONS.find((k) => k.value === rule.kind)?.label || rule.kind

  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{rule.name}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge tone={KIND_TONE[rule.kind] || 'slate'}>{kindLabel}</Badge>
            <Badge tone="slate">{SCOPE_LABEL[rule.scope] || rule.scope || '个股'}</Badge>
          </div>
        </div>

        {/* 启停开关：立即 PATCH {enabled}，失败只提示不回滚，用户可再点一次重试 */}
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-[11px] text-slate-400 dark:text-slate-500">{rule.enabled ? '启用' : '停用'}</span>
          <button
            type="button"
            role="switch"
            aria-checked={!!rule.enabled}
            aria-label={rule.enabled ? '停用规则' : '启用规则'}
            disabled={busy}
            onClick={() => onToggle(rule)}
            className={`relative h-5 w-9 rounded-full transition-colors disabled:opacity-60 ${
              rule.enabled ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600'
            }`}
          >
            <span
              className={`absolute top-0.5 size-4 rounded-full bg-white shadow transition-all ${
                rule.enabled ? 'left-4' : 'left-0.5'
              }`}
            />
          </button>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {chips.length === 0 ? (
          <span className="text-[11px] text-slate-400 dark:text-slate-500">未设参数 · 按规则默认口径触发</span>
        ) : (
          chips.map(([k, v]) => (
            <span
              key={k}
              className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] tabular-nums text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300"
            >
              {paramText(k, v)}
            </span>
          ))
        )}
      </div>

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-3 text-[11px] text-slate-400 dark:text-slate-500">
        <span className="tabular-nums">
          运行 {fmtNum(rule.run_count ?? 0, 0)} 次 · 最近 {rule.last_run_at ? fmtDateTime(rule.last_run_at) : '未运行'}
        </span>
        <span className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => onEdit(rule)}>
            编辑
          </Button>
          {confirming ? (
            <>
              <Button variant="danger" size="sm" loading={deleting} onClick={() => onConfirmDelete(rule)}>
                确认删除
              </Button>
              <Button variant="ghost" size="sm" onClick={onCancelDelete}>
                取消
              </Button>
            </>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => onConfirmDelete(rule)}>
              删除
            </Button>
          )}
        </span>
      </div>
    </Card>
  )
}

export default function AnomalyRulesPage() {
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('all')
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)
  const [toggleId, setToggleId] = useState(null)
  const [confirmId, setConfirmId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)

  const { data, loading, error, reload } = useApi(() => api.get('/anomaly/rules', { page, size: PAGE_SIZE }), [page])

  const items = data?.items || []
  const filtered = items.filter((r) =>
    filter === 'all' ? true : filter === 'enabled' ? !!r.enabled : !r.enabled,
  )
  const watched = items.filter((r) => r.scope !== 'symbol' || (r.params && r.params.ts_code)).length

  const setParam = (key, value) => setForm((f) => ({ ...f, params: { ...f.params, [key]: value } }))

  const openCreate = () => {
    setEditing(null)
    setForm({ ...EMPTY_FORM, params: { ...EMPTY_FORM.params } })
    setFormError('')
    setShowForm(true)
  }

  const openEdit = (rule) => {
    const p = rule.params || {}
    setEditing(rule)
    setConfirmId(null)
    setFormError('')
    setForm({
      name: rule.name || '',
      kind: rule.kind || 'rapid_rise',
      scope: rule.scope || 'symbol',
      ts_code: p.ts_code || '',
      params: {
        pct_chg: p.pct_chg ?? '',
        volume_ratio: p.volume_ratio ?? '',
        turnover_rate: p.turnover_rate ?? '',
        amount: p.amount ?? '',
        window_min: p.window_min ?? '',
      },
    })
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
    setFormError('')
  }

  const save = async () => {
    const name = form.name.trim()
    if (!name) {
      setFormError('请填写规则名称')
      return
    }
    const params = {}
    PARAM_FIELDS.forEach(({ key }) => {
      const raw = form.params[key]
      if (raw !== '' && raw !== null && raw !== undefined && !Number.isNaN(Number(raw))) params[key] = Number(raw)
    })
    const tsCode = form.ts_code.trim()
    if (tsCode) params.ts_code = tsCode

    const body = { name, kind: form.kind, scope: form.scope, params }
    setSaving(true)
    setFormError('')
    try {
      if (editing) {
        await api.patch(`/anomaly/rules/${editing.id}`, body)
        toast.success('规则已更新', name)
      } else {
        await api.post('/anomaly/rules', { ...body, enabled: true })
        toast.success('规则已创建', name)
      }
      closeForm()
      reload()
    } catch (e) {
      // 保留表单内容，用户修正后可直接重试
      setFormError(e.message)
      toast.error(editing ? '保存失败' : '创建失败', e.message)
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (rule) => {
    setToggleId(rule.id)
    try {
      await api.patch(`/anomaly/rules/${rule.id}`, { enabled: !rule.enabled })
      toast.success(rule.enabled ? '规则已停用' : '规则已启用', rule.name)
      reload()
    } catch (e) {
      toast.error('操作失败', e.message)
    } finally {
      setToggleId(null)
    }
  }

  const remove = async (rule) => {
    setDeletingId(rule.id)
    try {
      await api.del(`/anomaly/rules/${rule.id}`)
      toast.success('规则已删除', rule.name)
      setConfirmId(null)
      reload()
    } catch (e) {
      toast.error('删除失败', e.message)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <PageShell
      title="异动提醒规则"
      subtitle="规则写入异动扫描任务，命中后进入实时异动事件流；启停与参数改动即时生效。"
      actions={
        <Button size="sm" onClick={openCreate}>
          新增规则
        </Button>
      }
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="规则总数" value={fmtNum(data?.total ?? items.length, 0)} />
        <StatTile
          label="监控中"
          value={fmtNum(items.filter((r) => r.enabled).length, 0)}
          tone="text-brand-600 dark:text-brand-400"
        />
        <StatTile label="指定标的" value={fmtNum(watched, 0)} />
        <StatTile label="当前筛选" value={fmtNum(filtered.length, 0)} />
      </div>

      {showForm ? (
        <Card>
          <SectionHeader
            title={editing ? '编辑规则' : '新建规则'}
            subtitle="阈值留空表示沿用规则默认口径；数值型参数会以 JSON 存入 params 字段"
            action={
              <Button variant="ghost" size="sm" onClick={closeForm}>
                收起
              </Button>
            }
          />
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="规则名称">
              <input
                className={INPUT}
                value={form.name}
                placeholder="如：开盘半小时急拉"
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </Field>
            <Field label="异动类型">
              <Select
                className="w-full"
                value={form.kind}
                options={KIND_OPTIONS}
                onChange={(v) => setForm((f) => ({ ...f, kind: v }))}
              />
            </Field>
            <Field label="监控范围">
              <Select
                className="w-full"
                value={form.scope}
                options={SCOPE_OPTIONS}
                onChange={(v) => setForm((f) => ({ ...f, scope: v }))}
              />
            </Field>
            <Field label="指定标的（可选）" hint="填写 ts_code，如 600519.SH；留空表示范围内全部标的">
              <input
                className={INPUT}
                value={form.ts_code}
                placeholder="600519.SH"
                onChange={(e) => setForm((f) => ({ ...f, ts_code: e.target.value }))}
              />
            </Field>
            {PARAM_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <input
                  className={INPUT}
                  inputMode="decimal"
                  value={form.params[f.key]}
                  placeholder={f.placeholder}
                  onChange={(e) => setParam(f.key, e.target.value)}
                />
              </Field>
            ))}
          </div>

          {formError ? (
            <p className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
              保存失败：{formError}
            </p>
          ) : null}

          <div className="mt-3 flex gap-2">
            <Button loading={saving} onClick={save}>
              {editing ? '保存修改' : '创建规则'}
            </Button>
            <Button variant="secondary" onClick={closeForm}>
              取消
            </Button>
          </div>
        </Card>
      ) : null}

      <Card>
        <SectionHeader
          title="规则列表"
          subtitle="点击开关即时启停；删除需要二次确认"
          action={
            <Tabs
              tabs={[
                { value: 'all', label: '全部' },
                { value: 'enabled', label: '已启用' },
                { value: 'disabled', label: '已停用' },
              ]}
              value={filter}
              onChange={setFilter}
            />
          }
        />

        {!data ? (
          <AsyncBlock loading={loading} error={error} onRetry={reload} loadingLabel="规则加载中…" />
        ) : filtered.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              text={items.length === 0 ? '暂无异动规则' : '当前筛选下没有规则'}
              hint={items.length === 0 ? '新建规则后由扫描任务在交易时段自动命中' : '切换上方筛选条件查看其它规则'}
            />
          </div>
        ) : (
          <>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {filtered.map((rule) => (
                <RuleCard
                  key={rule.id}
                  rule={rule}
                  busy={toggleId === rule.id}
                  confirming={confirmId === rule.id}
                  deleting={deletingId === rule.id}
                  onToggle={toggle}
                  onEdit={openEdit}
                  onConfirmDelete={(r) => (confirmId === r.id ? remove(r) : setConfirmId(r.id))}
                  onCancelDelete={() => setConfirmId(null)}
                />
              ))}
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={data.total || 0} onChange={setPage} />
          </>
        )}
      </Card>
    </PageShell>
  )
}
