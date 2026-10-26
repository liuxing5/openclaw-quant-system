/**
 * /workbench/settings/general 工作台 · 基础设置（管理端）。
 *
 * 原站这条路由没有登记（spec/pages/_index.md 标为「路由未登记」），本地已注册，
 * 因此落地为管理端版本的通用设置：字段比 /settings/general 更全（平台名、公告、
 * 注册开关、在线人数等），读写都走 GET/PUT /settings/all，PUT body 拍平成 {key:value}。
 *
 * 为什么列出「默认值」徽标：GET 里没有该键时表单显示的是内置默认值，
 * 必须标注清楚，避免把默认值误当成后端已保存的配置。
 */
import React, { useEffect, useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { toast } from '../lib/toast.jsx'

/** 分组卡片定义：type 决定控件，paths 是 GET 响应里可能的取值路径（支持点号）。 */
const GROUPS = [
  {
    key: 'site',
    title: '平台信息',
    desc: '展示在顶栏、登录页与公告条',
    fields: [
      { key: 'platform_name', label: '平台名称', paths: ['platform_name', 'site_name', 'site.platform_name', 'site.site_name'], type: 'text', default: '量化投研一体化平台', placeholder: '例如：量化投研一体化平台' },
      { key: 'slogan', label: '站点标语', paths: ['slogan', 'site.slogan'], type: 'text', default: '股票投研 · 数据一体化 · 本地数据驱动', placeholder: '一句话介绍平台' },
      { key: 'announcement', label: '公告', paths: ['announcement', 'site.announcement'], type: 'textarea', default: '', placeholder: '面向所有用户的公告，留空则不展示' },
      { key: 'icp', label: '备案号', paths: ['icp', 'site.icp'], type: 'text', default: '', placeholder: '例如：京ICP备xxxxxxxx号' },
    ],
  },
  {
    key: 'ops',
    title: '运营开关',
    desc: '注册、在线人数与维护模式',
    fields: [
      { key: 'register_enabled', label: '开放注册', paths: ['register_enabled', 'site.register_enabled'], type: 'switch', default: true, hint: '关闭后登录页不再提供注册入口' },
      { key: 'maintenance', label: '维护模式', paths: ['maintenance', 'site.maintenance'], type: 'switch', default: false, hint: '开启后普通用户只读，仅管理员可操作' },
      { key: 'online_users', label: '在线人数', paths: ['online_users', 'site.online_users'], type: 'number', default: 1, hint: '顶栏展示的在线人数（本地环境为单用户）' },
      { key: 'first_register_bonus_days', label: '首注赠送天数', paths: ['first_register_bonus_days', 'site.first_register_bonus_days'], type: 'number', default: 1, hint: '新用户注册后赠送的套餐天数' },
    ],
  },
]

const ALL_FIELDS = GROUPS.flatMap((g) => g.fields)

/** 按候选路径依次取值，全部缺失才返回 undefined。 */
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

function Switch({ on, onToggle, label }) {
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

/** 单个字段：控件 + 标签 + 默认值徽标。 */
function Field({ field, value, provided, onChange }) {
  const baseInput =
    'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'

  return (
    <div className="border-b border-dashed border-slate-100 py-3 last:border-b-0 dark:border-slate-800">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-slate-700 dark:text-slate-200">
            {field.label}
            <code className="font-mono text-[10px] text-slate-400">{field.key}</code>
            {!provided ? <Badge tone="amber">默认值</Badge> : null}
          </p>
          {field.hint ? (
            <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{field.hint}</p>
          ) : null}
        </div>
        <div className="w-1/2 max-w-md shrink-0">
          {field.type === 'switch' ? (
            <div className="flex justify-end">
              <Switch on={Boolean(value)} label={field.label} onToggle={() => onChange(!value)} />
            </div>
          ) : field.type === 'textarea' ? (
            <textarea
              rows={2}
              value={value ?? ''}
              placeholder={field.placeholder}
              onChange={(e) => onChange(e.target.value)}
              className={baseInput}
            />
          ) : field.type === 'number' ? (
            <input
              type="number"
              value={value ?? 0}
              placeholder={field.placeholder}
              onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
              className={`${baseInput} text-right tabular-nums`}
            />
          ) : (
            <input
              type="text"
              value={value ?? ''}
              placeholder={field.placeholder}
              onChange={(e) => onChange(e.target.value)}
              className={baseInput}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default function WorkbenchSettingsGeneralPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/settings/all'), [])
  const [values, setValues] = useState({})
  const [provided, setProvided] = useState({})
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  // 接口数据到位后回填表单；缺键的字段落到内置默认值并打「默认值」徽标
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

  const setValue = (key, v) => {
    setValues((prev) => ({ ...prev, [key]: v }))
    setDirty(true)
  }

  const save = async () => {
    if (saving) return
    setSaving(true)
    // PUT body 拍平成 {key: value}，与契约 /settings/all 的批量写一致
    const body = {}
    ALL_FIELDS.forEach((f) => {
      let v = values[f.key]
      if (f.type === 'number' && v !== '' && v !== null && v !== undefined) v = Number(v)
      if (f.type === 'switch') v = Boolean(v)
      body[f.key] = v
    })
    try {
      await api.put('/settings/all', body)
      toast.success('保存成功', `已写入 ${Object.keys(body).length} 个设置项，正在回读校验`)
      setDirty(false)
      reload()
    } catch (e) {
      // 失败不清空表单：内容还在，点「保存全部」可直接重试
      toast.error('保存失败', `${e.message || '请求失败'}，表单内容已保留，可再次点击「保存全部」重试`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageShell
      title="基础设置（工作台）"
      subtitle="管理端通用设置 · 读写 GET/PUT /settings/all · 原站此路径未登记，本地为管理端版本"
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
        loadingLabel="设置加载中"
        empty={!loading && !error && !data}
        emptyText="暂无设置项"
        emptyHint="接口返回空对象时会展示全部内置默认值"
      >
        <div className="space-y-4">
          {GROUPS.map((g) => (
            <Card key={g.key}>
              <SectionHeader
                title={g.title}
                subtitle={g.desc}
                badge={<Badge tone="slate">{g.fields.length} 项</Badge>}
              />
              <div className="mt-1">
                {g.fields.map((f) => (
                  <Field
                    key={f.key}
                    field={f}
                    value={values[f.key]}
                    provided={provided[f.key]}
                    onChange={(v) => setValue(f.key, v)}
                  />
                ))}
              </div>
            </Card>
          ))}

          <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-[11px] text-slate-500 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
            <span>
              保存会把上表全部字段一次性 PUT 到 <code className="font-mono">/settings/all</code>，
              后端按 key 落库（app_settings）。
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
