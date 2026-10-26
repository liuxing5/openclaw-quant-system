/**
 * AI 设置（/settings/ai）：模型、生成参数、自动化任务与每日推送。
 *
 * 表单内部按 `{ai: {...}}` 结构化保存（便于分组扩展），提交前用 flatten() 拍平成
 * `{key: value}` 字典——与 PUT /settings/all 的契约一致（如 `ai.model`、`ai.temperature`）。
 */
import { useEffect, useState } from 'react'
import { AsyncBlock, Button, Card, ErrorState, PageShell, SectionHeader, Select } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { toast } from '../lib/toast.jsx'

const INPUT_CLS =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'

const MODEL_OPTIONS = [
  { value: 'glm-4.6', label: 'GLM-4.6 · 中文投研语料最稳' },
  { value: 'glm-4-air', label: 'GLM-4-Air · 便宜快，适合批量' },
  { value: 'qwen-plus', label: 'Qwen-Plus · 结构化输出好' },
  { value: 'deepseek-v3', label: 'DeepSeek-V3 · 推理能力强' },
  { value: 'gpt-4o-mini', label: 'GPT-4o-mini · 英文资讯更准' },
]

const DEFAULTS = {
  ai: {
    model: 'glm-4.6',
    temperature: 0.7,
    auto_screen: true,
    review_auto_generate: true,
    push_enabled: true,
    daily_push_time: '17:30',
  },
}

const GROUPS = [
  {
    title: '模型与生成参数',
    subtitle: '影响 AI 自动选股理由与复盘文案的生成方式',
    keys: ['ai.model', 'ai.temperature'],
  },
  {
    title: '自动化任务',
    subtitle: '盘后自动执行的 AI 作业，关掉后只能手动触发',
    keys: ['ai.auto_screen', 'ai.review_auto_generate'],
  },
  {
    title: '每日推送',
    subtitle: '复盘报告与选股结果的推送节奏',
    keys: ['ai.push_enabled', 'ai.daily_push_time'],
  },
]

const FIELD_META = {
  'ai.model': { label: '默认模型', type: 'select', hint: '策略命中理由、复盘正文统一使用该模型' },
  'ai.temperature': { label: '温度（随机度）', type: 'range', hint: '0 更严谨 / 1 更发散，复盘文案建议 0.3–0.6' },
  'ai.auto_screen': { label: '自动选股', type: 'switch', hint: '交易日盘后按启用策略自动扫描并汇总命中' },
  'ai.review_auto_generate': { label: '复盘自动生成', type: 'switch', hint: '收盘后自动撰写当日复盘并打情绪分' },
  'ai.push_enabled': { label: '每日推送', type: 'switch', hint: '到点汇总复盘与选股结果推送一次' },
  'ai.daily_push_time': { label: '推送时间', type: 'time', hint: '24 小时制，建议收盘复盘完成后（17:00 后）' },
}

/** 把嵌套对象拍平成 {key: value}，key 形如 ai.temperature */
function flatten(obj, prefix = '', out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, key, out)
    else out[key] = v
  }
  return out
}

/** 接口可能是 {items:[{key,value}]} 或嵌套对象，先拍平再按 ai.* 取值 */
function flattenSettings(raw) {
  if (!raw || typeof raw !== 'object') return {}
  if (Array.isArray(raw.items)) {
    return Object.fromEntries(raw.items.map((i) => [i.key, i.value]))
  }
  return flatten(raw)
}

function pickForm(flat) {
  const next = { ai: { ...DEFAULTS.ai } }
  for (const [k, v] of Object.entries(flat)) {
    const [ns, key] = k.split('.')
    if (ns === 'ai' && key && v !== undefined && v !== null) next.ai[key] = v
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

function FieldRow({ path, value, onChange }) {
  const meta = FIELD_META[path]
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-slate-700 dark:text-slate-200">{meta.label}</p>
        <p className="mt-0.5 text-[11px] leading-snug text-slate-400 dark:text-slate-500">{meta.hint}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {meta.type === 'switch' ? (
          <Switch checked={!!value} onChange={onChange} label={meta.label} />
        ) : meta.type === 'select' ? (
          <Select
            value={value}
            onChange={onChange}
            options={MODEL_OPTIONS}
            className="w-56 max-w-full"
            aria-label={meta.label}
          />
        ) : meta.type === 'range' ? (
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={0}
              max={1}
              step={0.1}
              value={Number(value)}
              onChange={(e) => onChange(Number(e.target.value))}
              aria-label={meta.label}
              className="w-36 accent-brand-600"
            />
            <span className="w-9 text-right text-xs font-semibold tabular-nums text-slate-700 dark:text-slate-200">
              {Number(value).toFixed(1)}
            </span>
          </div>
        ) : (
          <input
            type="time"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            aria-label={meta.label}
            className={`${INPUT_CLS} w-32 tabular-nums`}
          />
        )}
      </div>
    </div>
  )
}

export default function SettingsAiPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/settings/all'), [])
  const [form, setForm] = useState(null)
  const [base, setBase] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState(null)

  useEffect(() => {
    if (!data) return
    const next = pickForm(flattenSettings(data))
    setForm(next)
    setBase(next)
  }, [data])

  const flat = form ? flatten(form) : null
  const flatBase = base ? flatten(base) : null
  const dirty = !!flat && !!flatBase && Object.keys(flat).some((k) => flat[k] !== flatBase[k])

  function setValue(path, v) {
    const [ns, key] = path.split('.')
    setForm((prev) => (prev ? { ...prev, [ns]: { ...prev[ns], [key]: v } } : prev))
  }

  async function save() {
    const changed = {}
    for (const [k, v] of Object.entries(flat)) {
      if (flatBase[k] !== v) changed[k] = v
    }
    if (!Object.keys(changed).length) {
      toast.info('没有需要保存的修改', '改动任意字段后再点击保存')
      return
    }
    setSaving(true)
    setSaveErr(null)
    try {
      await api.put('/settings/all', changed) // 已是 {key:value} 扁平字典
      toast.success('AI 设置已保存', `已写入 ${Object.keys(changed).length} 项配置`)
      setBase(form)
      reload()
    } catch (e) {
      setSaveErr(e)
      toast.error('保存失败，配置已回滚', (e && e.message) || '线上仍为修改前的配置，可直接重试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageShell
      title="AI 设置"
      subtitle="模型、自动化任务与每日推送"
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
        emptyText="暂无 AI 配置"
        onRetry={reload}
        loadingLabel="AI 配置加载中"
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
                  {g.keys.map((path) => (
                    <FieldRow
                      key={path}
                      path={path}
                      value={flat[path]}
                      onChange={(v) => setValue(path, v)}
                    />
                  ))}
                </div>
              </Card>
            ))}

            <Card>
              <SectionHeader title="生效说明" subtitle="配置对全站 AI 作业生效" />
              <p className="mt-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
                保存时表单会按{' '}
                <code className="text-[11px] text-brand-600 dark:text-brand-300">{'{key: value}'}</code>{' '}
                拍平后批量提交到 /settings/all，键名形如{' '}
                <code className="text-[11px] text-brand-600 dark:text-brand-300">ai.model</code> /{' '}
                <code className="text-[11px] text-brand-600 dark:text-brand-300">ai.temperature</code>；
                关闭自动化任务后，对应作业只在手动触发时执行。
              </p>
            </Card>
          </div>
        ) : null}
      </AsyncBlock>
    </PageShell>
  )
}
