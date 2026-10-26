/**
 * /workbench/settings/ai 工作台 · AI 设置（管理端）。
 *
 * 原站这条路由没有登记（spec/pages/_index.md 标为「路由未登记」），本地已注册，
 * 因此落地为管理端版本的 AI 参数页：模型、温度、自动选股、复盘自动生成、推送时间。
 * 读写都走 GET/PUT /settings/all，PUT body 拍平成 {key: value}（与基础设置页
 * 各自独立实现，不共用组件，便于两页后续独立演进）。
 *
 * 为什么顶部有「参数速览」：改完模型/温度后需要一眼确认当前生效值，
 * 表单较长时速览区是最省眼力的核对面。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, SectionHeader, StatTile } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { toast } from '../lib/toast.jsx'

const MODEL_OPTIONS = ['glm-4.6', 'glm-4.5', 'deepseek-chat', 'qwen-max', 'gpt-4o-mini']

const GROUPS = [
  {
    key: 'model',
    title: '模型参数',
    desc: '影响复盘撰写、持仓解读与问答回复',
    fields: [
      {
        key: 'ai_model',
        label: 'AI 模型',
        paths: ['ai_model', 'ai.model', 'model', 'llm_model'],
        type: 'select',
        default: 'glm-4.6',
        hint: '复盘生成与问答统一使用的模型标识',
      },
      {
        key: 'ai_temperature',
        label: '温度 temperature',
        paths: ['ai_temperature', 'ai.temperature', 'temperature'],
        type: 'number',
        default: 0.7,
        min: 0,
        max: 2,
        step: 0.1,
        hint: '0 = 输出稳定收敛，2 = 更发散；复盘类任务建议 0.3 ~ 0.8',
      },
    ],
  },
  {
    key: 'auto',
    title: '自动化任务',
    desc: '选股与复盘是否由系统自动跑',
    fields: [
      {
        key: 'auto_pick_enabled',
        label: '自动选股',
        paths: ['auto_pick_enabled', 'auto_stock_pick', 'strategy_auto_pick', 'auto_pick'],
        type: 'switch',
        default: true,
        hint: '交易日收盘后自动执行内置策略并生成每日选股池',
      },
      {
        key: 'review_auto_generate',
        label: '复盘自动生成',
        paths: ['review_auto_generate', 'auto_review', 'review.auto_generate'],
        type: 'switch',
        default: true,
        hint: '每日收盘后按当日行情自动撰写复盘，无需手动点生成',
      },
    ],
  },
  {
    key: 'push',
    title: '推送计划',
    desc: '报告与提醒的统一推送时间',
    fields: [
      {
        key: 'push_time',
        label: '推送时间',
        paths: ['push_time', 'notify_time', 'report_push_time', 'push.time'],
        type: 'time',
        default: '08:30',
        hint: '每个交易日该时刻推送复盘摘要与提醒（HH:MM）',
      },
    ],
  },
]

const ALL_FIELDS = GROUPS.flatMap((g) => g.fields)

function readValue(raw, paths) {
  if (!raw) return undefined
  for (const p of paths) {
    if (Object.prototype.hasOwnProperty.call(raw, p)) {
      const v = raw[p]
      if (v !== undefined && v !== null) return v
    }
    const v = p.split('.').reduce((acc, k) => (acc === undefined || acc === null ? undefined : acc[k]), raw)
    if (v !== undefined && v !== null) return v
  }
  return undefined
}

function Toggle({ on, label, onToggle }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onToggle}
      className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${on ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-700'}`}
    >
      <span className={`size-4 rounded-full bg-white shadow-sm transition-transform ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  )
}

function AiField({ field, value, provided, onChange }) {
  const inputCls =
    'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'

  // 模型下拉：后端存的值不在内置清单里时补一项，避免打开后看不到真实值
  const options = useMemo(() => {
    const list = [...MODEL_OPTIONS]
    if (field.type === 'select' && value && !list.includes(String(value))) list.unshift(String(value))
    return list
  }, [field.type, value])

  return (
    <div className="grid gap-2 border-b border-dashed border-slate-100 py-3 last:border-b-0 dark:border-slate-800 sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-slate-700 dark:text-slate-200">
          {field.label}
          <code className="font-mono text-[10px] text-slate-400">{field.key}</code>
          {!provided ? <Badge tone="amber">默认值</Badge> : null}
        </p>
        <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{field.hint}</p>
      </div>
      <div className="w-full sm:w-64">
        {field.type === 'switch' ? (
          <div className="flex items-center justify-end gap-2">
            <span className={`text-[11px] ${value ? 'text-brand-600 dark:text-brand-400' : 'text-slate-400'}`}>
              {value ? '已开启' : '已关闭'}
            </span>
            <Toggle on={Boolean(value)} label={field.label} onToggle={() => onChange(!value)} />
          </div>
        ) : field.type === 'select' ? (
          <select value={value ?? ''} onChange={(e) => onChange(e.target.value)} className={inputCls}>
            {options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        ) : field.type === 'time' ? (
          <input type="time" value={value ?? ''} onChange={(e) => onChange(e.target.value)} className={`${inputCls} tabular-nums`} />
        ) : (
          <input
            type="number"
            min={field.min}
            max={field.max}
            step={field.step}
            value={value ?? 0}
            onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
            className={`${inputCls} text-right tabular-nums`}
          />
        )}
      </div>
    </div>
  )
}

export default function WorkbenchSettingsAiPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/settings/all'), [])
  const [values, setValues] = useState({})
  const [provided, setProvided] = useState({})
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!data) return
    const next = {}
    const flags = {}
    ALL_FIELDS.forEach((f) => {
      const v = readValue(data, f.paths)
      const has = v !== undefined && v !== ''
      next[f.key] = has ? v : f.default
      flags[f.key] = has
    })
    setValues(next)
    setProvided(flags)
    setDirty(false)
  }, [data])

  const change = (key, v) => {
    setValues((prev) => ({ ...prev, [key]: v }))
    setDirty(true)
  }

  const save = async () => {
    if (saving) return
    setSaving(true)
    const body = {}
    ALL_FIELDS.forEach((f) => {
      let v = values[f.key]
      if (f.type === 'number' && v !== '' && v !== null && v !== undefined) {
        const n = Number(v)
        if (Number.isFinite(n)) {
          const min = f.min ?? -Infinity
          const max = f.max ?? Infinity
          v = Math.min(max, Math.max(min, n))
        }
      }
      if (f.type === 'switch') v = Boolean(v)
      body[f.key] = v
    })
    try {
      await api.put('/settings/all', body)
      toast.success('AI 设置已保存', `已写入 ${Object.keys(body).length} 个参数，正在回读校验`)
      setDirty(false)
      reload()
    } catch (e) {
      toast.error('保存失败', `${e.message || '请求失败'}，表单内容已保留，可再次点击「保存全部」重试`)
    } finally {
      setSaving(false)
    }
  }

  const autoCount = ['auto_pick_enabled', 'review_auto_generate'].filter((k) => Boolean(values[k])).length

  return (
    <PageShell
      title="AI 设置（工作台）"
      subtitle="管理端 AI 参数 · 读写 GET/PUT /settings/all · 原站此路径未登记，本地为管理端版本"
      actions={
        <div className="flex items-center gap-2">
          {data?.meta ? (
            <Badge tone={data.meta.degraded ? 'rose' : data.meta.seed ? 'amber' : 'slate'}>
              {data.meta.degraded ? '降级数据' : data.meta.seed ? '种子演示数据' : `来源 ${data.meta.source || '—'}`}
            </Badge>
          ) : null}
          {dirty ? <Badge tone="amber">有未保存改动</Badge> : null}
          <Button onClick={save} loading={saving}>
            保存全部
          </Button>
        </div>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={reload}
        loadingLabel="AI 设置加载中"
        empty={!loading && !error && !data}
        emptyText="暂无设置项"
        emptyHint="接口返回空对象时会展示全部内置默认值"
      >
        <div className="space-y-4">
          {/* 参数速览：确认当前生效值 */}
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <StatTile label="当前模型" value={values.ai_model || '—'} sub={provided.ai_model ? '接口返回值' : '内置默认值'} />
            <StatTile label="温度" value={values.ai_temperature ?? '—'} sub="0 ~ 2" />
            <StatTile
              label="自动化任务"
              value={`${autoCount}/2`}
              sub="自动选股 · 复盘生成"
              tone={autoCount ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}
            />
            <StatTile label="推送时间" value={values.push_time || '—'} sub="每个交易日" />
          </div>

          {GROUPS.map((g) => (
            <Card key={g.key}>
              <SectionHeader
                title={g.title}
                subtitle={g.desc}
                badge={<Badge tone="slate">{g.fields.length} 项</Badge>}
              />
              <div className="mt-1">
                {g.fields.map((f) => (
                  <AiField
                    key={f.key}
                    field={f}
                    value={values[f.key]}
                    provided={provided[f.key]}
                    onChange={(v) => change(f.key, v)}
                  />
                ))}
              </div>
            </Card>
          ))}

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-[11px] text-slate-500 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
            <span>
              保存会把模型 / 温度 / 自动化 / 推送一次性 PUT 到{' '}
              <code className="font-mono">/settings/all</code>，温度会按 0 ~ 2 夹取后再提交。
            </span>
            <Button size="sm" variant="secondary" onClick={save} loading={saving}>
              {dirty ? '保存全部 *' : '保存全部'}
            </Button>
          </div>
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
