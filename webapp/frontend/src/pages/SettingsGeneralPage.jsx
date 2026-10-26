/**
 * 基础设置（/settings/general）：平台名称、公告、注册开关等系统级配置。
 *
 * 读写都走 /settings/all：GET 返回的可能是 {key:value}、嵌套对象或 {items:[{key,value}]}，
 * 统一拍平成扁平字典再填表单；PUT 按契约要求只提交 `{key: value}` 的变更键，
 * 失败时把表单回滚成上次加载的值并给出可重试的错误态。
 */
import { useEffect, useState } from 'react'
import { AsyncBlock, Button, Card, ErrorState, PageShell, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { toast } from '../lib/toast.jsx'

const INPUT_CLS =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'

const GROUPS = [
  {
    title: '平台信息',
    subtitle: '落地页与侧边栏展示的基础信息',
    fields: [
      { key: 'platform_name', label: '平台名称', type: 'text', placeholder: '量化投研平台', hint: '侧边栏与落地页顶部展示的名称' },
      { key: 'announcement', label: '公告', type: 'textarea', placeholder: '例如：今日更新域名 xxx', hint: '落地页公告条内容，留空则不展示' },
      { key: 'logo_path', label: 'Logo 地址', type: 'text', placeholder: 'https://…/logo.png（留空用文字标识）', hint: '站内标识图，缺省时显示文字标识' },
    ],
  },
  {
    title: '注册与展示',
    subtitle: '注册开关与落地页统计口径',
    fields: [
      { key: 'register_enabled', label: '开放注册', type: 'switch', hint: '关闭后新用户无法注册账号，已有账号不受影响' },
      { key: 'marketing_total_users', label: '在线 / 累计人数', type: 'number', placeholder: '3000', hint: '落地页「累计服务 N 用户」徽标读取该值' },
    ],
  },
]

const DEFAULTS = { platform_name: '', announcement: '', logo_path: '', register_enabled: true, marketing_total_users: 0 }

const ALL_KEYS = GROUPS.flatMap((g) => g.fields.map((f) => f.key))

/** 接口返回形态兜底：把 {items} / 嵌套对象统一拍平成 {key:value} */
function flattenSettings(raw) {
  if (!raw || typeof raw !== 'object') return {}
  const src = Array.isArray(raw.items)
    ? Object.fromEntries(raw.items.map((i) => [i.key, i.value]))
    : raw.settings && typeof raw.settings === 'object'
      ? raw.settings
      : raw
  const out = {}
  const walk = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k
      if (v && typeof v === 'object' && !Array.isArray(v)) walk(v, key)
      else out[key] = v
    }
  }
  walk(src, '')
  return out
}

function pickForm(flat) {
  const next = {}
  for (const key of ALL_KEYS) {
    const v = flat[key]
    next[key] = v === undefined || v === null ? DEFAULTS[key] : v
  }
  return next
}

function Switch({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
        checked ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600'
      }`}
    >
      <span
        className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${
          checked ? 'left-[22px]' : 'left-0.5'
        }`}
      />
    </button>
  )
}

function FieldRow({ field, value, onChange }) {
  const base =
    'rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50'
  return (
    <div className={field.type === 'textarea' ? base : `${base} flex items-center justify-between gap-3`}>
      <div className={field.type === 'textarea' ? '' : 'min-w-0'}>
        <p className="text-xs font-medium text-slate-700 dark:text-slate-200">{field.label}</p>
        <p className="mt-0.5 text-[11px] leading-snug text-slate-400 dark:text-slate-500">{field.hint}</p>
      </div>
      <div className={field.type === 'textarea' ? 'mt-2' : 'shrink-0'}>
        {field.type === 'textarea' ? (
          <textarea
            rows={3}
            value={value ?? ''}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
            className={INPUT_CLS}
          />
        ) : field.type === 'switch' ? (
          <Switch checked={!!value} onChange={onChange} label={field.label} />
        ) : (
          <input
            type={field.type === 'number' ? 'number' : 'text'}
            value={value ?? ''}
            placeholder={field.placeholder}
            onChange={(e) =>
              onChange(field.type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)
            }
            className={`${INPUT_CLS} w-44 text-right tabular-nums max-sm:w-full`}
          />
        )}
      </div>
    </div>
  )
}

export default function SettingsGeneralPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/settings/all'), [])
  const [form, setForm] = useState(null)
  const [base, setBase] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState(null)

  // 服务端数据到位后填表，同时留一份基线用于脏值比较与失败回滚
  useEffect(() => {
    if (!data) return
    const next = pickForm(flattenSettings(data))
    setForm(next)
    setBase(next)
  }, [data])

  const dirty = !!form && !!base && ALL_KEYS.some((k) => form[k] !== base[k])

  function setValue(key, v) {
    setForm((prev) => (prev ? { ...prev, [key]: v } : prev))
  }

  async function save() {
    const changed = {}
    for (const key of ALL_KEYS) {
      if (form[key] !== base[key]) changed[key] = form[key]
    }
    if (!Object.keys(changed).length) {
      toast.info('没有需要保存的修改', '改动任意字段后再点击保存')
      return
    }
    setSaving(true)
    setSaveErr(null)
    try {
      await api.put('/settings/all', changed)
      toast.success('设置已保存', `已写入 ${Object.keys(changed).length} 项配置`)
      setBase(form)
      reload()
    } catch (e) {
      // 失败 = 服务端配置仍是上次加载的值（未生效），保留表单改动供重试
      setSaveErr(e)
      toast.error('保存失败，配置已回滚', (e && e.message) || '线上仍为修改前的配置，可直接重试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageShell
      title="基础设置"
      subtitle="平台信息、公告与注册开关"
      actions={
        <div className="flex items-center gap-2">
          {dirty ? <span className="text-[11px] text-amber-600 dark:text-amber-400">有未保存的修改</span> : null}
          <Button variant="secondary" size="sm" onClick={reload} disabled={saving}>
            重新加载
          </Button>
          <Button size="sm" onClick={save} loading={saving} disabled={!form || !dirty}>
            保存设置
          </Button>
        </div>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        empty={!data}
        emptyText="暂无系统设置"
        onRetry={reload}
        loadingLabel="系统设置加载中"
      >
        {form ? (
          <div className="space-y-4">
            {saveErr ? (
              <ErrorState
                error={saveErr}
                onRetry={save}
                text={`${saveErr.message || '保存失败'}：线上配置仍为上次加载的值（未生效），可修改后重试`}
              />
            ) : null}

            {GROUPS.map((g) => (
              <Card key={g.title}>
                <SectionHeader title={g.title} subtitle={g.subtitle} />
                <div className="mt-3 space-y-2.5">
                  {g.fields.map((f) => (
                    <FieldRow key={f.key} field={f} value={form[f.key]} onChange={(v) => setValue(f.key, v)} />
                  ))}
                </div>
              </Card>
            ))}

            <Card>
              <SectionHeader title="生效范围" subtitle="这些配置对全站所有用户生效" />
              <p className="mt-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
                保存会把改动的键值以 <code className="text-[11px] text-brand-600 dark:text-brand-300">{'{key: value}'}</code>{' '}
                字典形式批量提交到 /settings/all；落地页公告、平台名与注册开关读取的是
                /settings/public 的公开投影，保存成功后刷新页面即可看到新值。
              </p>
            </Card>
          </div>
        ) : null}
      </AsyncBlock>
    </PageShell>
  )
}
