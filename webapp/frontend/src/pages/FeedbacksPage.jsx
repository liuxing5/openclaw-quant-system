/**
 * 意见反馈（/feedbacks）：左侧提交表单（分类 + 内容 + 联系方式），右侧我的反馈列表。
 *
 * 写操作：POST /feedbacks 提交、DELETE /feedbacks/{id} 删除（契约同时约定 PATCH 撤回，
 * 后端若只实现 PATCH，DELETE 会返回 405，此处自动降级到 PATCH），成功后 toast + reload 列表。
 */
import { useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  ErrorState,
  PageShell,
  Pagination,
  SectionHeader,
  TextLink,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const MAX_LEN = 500

const CATEGORIES = [
  { value: 'bug', label: '问题反馈', desc: '功能不可用、报错、数据不刷新' },
  { value: 'idea', label: '功能建议', desc: '希望新增或改进的能力' },
  { value: 'data', label: '数据纠错', desc: '行情、财报、板块数据有误' },
  { value: 'ui', label: '界面体验', desc: '布局、交互、移动端适配问题' },
  { value: 'other', label: '其他', desc: '以上都不合适的主题' },
]

const STATUS_MAP = {
  open: { tone: 'amber', label: '待处理' },
  replied: { tone: 'emerald', label: '已回复' },
  closed: { tone: 'slate', label: '已关闭' },
  withdrawn: { tone: 'slate', label: '已撤回' },
}

function categoryLabel(value) {
  return (CATEGORIES.find((c) => c.value === value) || {}).label || value || '—'
}

/* --------------------------------------------------------------- 局部小组件 */

function StatusBadge({ status }) {
  const s = STATUS_MAP[status] || { tone: 'slate', label: status || '未知' }
  return <Badge tone={s.tone}>{s.label}</Badge>
}

/** 删除：DELETE 优先，405（后端只实现契约里的 PATCH 撤回）时降级 PATCH */
async function removeFeedback(id) {
  try {
    await api.delete(`/feedbacks/${id}`)
  } catch (e) {
    if (e && e.status === 405) await api.patch(`/feedbacks/${id}`, { status: 'withdrawn' })
    else throw e
  }
}

function FeedbackItem({ item, onDeleted }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function doDelete() {
    setBusy(true)
    setErr(null)
    try {
      await removeFeedback(item.id)
      toast.success('已删除反馈', categoryLabel(item.category))
      onDeleted()
    } catch (e) {
      setErr(e)
      toast.error('删除失败', (e && e.message) || '请稍后重试')
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  return (
    <li className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="blue">{categoryLabel(item.category)}</Badge>
        <StatusBadge status={item.status} />
        <span className="ml-auto text-[11px] text-slate-400 dark:text-slate-500">
          {fmtDateTime(item.created_at || item.createdAt)}
        </span>
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700 dark:text-slate-200">
        {item.content}
      </p>
      {item.contact ? (
        <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">联系方式：{item.contact}</p>
      ) : null}

      {item.admin_reply ? (
        <div className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2 dark:border-emerald-500/30 dark:bg-emerald-500/10">
          <p className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
            管理员回复 · {fmtDateTime(item.replied_at)}
          </p>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-emerald-800 dark:text-emerald-200">
            {item.admin_reply}
          </p>
        </div>
      ) : null}

      {err ? <div className="mt-2"><ErrorState error={err} onRetry={doDelete} /></div> : null}

      <div className="mt-2 flex items-center justify-end gap-2">
        {confirming ? (
          <>
            <span className="mr-auto text-[11px] text-slate-400">删除后不可恢复，确认？</span>
            <Button variant="danger" size="sm" loading={busy} onClick={doDelete}>
              确认删除
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirming(false)}>
              取消
            </Button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
          >
            删除
          </button>
        )}
      </div>
    </li>
  )
}

/* ---------------------------------------------------------------------- 表单 */

function SubmitForm({ onSubmitted }) {
  const [category, setCategory] = useState('bug')
  const [content, setContent] = useState('')
  const [contact, setContact] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState(null)

  const canSubmit = content.trim().length > 0 && !submitting

  async function submit() {
    if (!content.trim()) {
      setErr(new Error('请先填写反馈内容'))
      return
    }
    setSubmitting(true)
    setErr(null)
    try {
      await api.post('/feedbacks', {
        category,
        content: content.trim(),
        contact: contact.trim() || undefined,
      })
      toast.success('反馈已提交', '处理进度可在右侧或「我的反馈」查看')
      setContent('')
      setContact('')
      onSubmitted()
    } catch (e) {
      setErr(e)
      toast.error('提交失败', (e && e.message) || '请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-3">
      {/* 分类：左侧竖排单选 */}
      <div>
        <p className="mb-1.5 text-xs font-medium text-slate-600 dark:text-slate-300">
          反馈分类 <span className="text-rose-500">*</span>
        </p>
        <div className="space-y-1.5">
          {CATEGORIES.map((c) => {
            const active = c.value === category
            return (
              <button
                key={c.value}
                type="button"
                onClick={() => setCategory(c.value)}
                className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition ${
                  active
                    ? 'border-brand-500 bg-brand-50 dark:bg-brand-500/10'
                    : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-700 dark:bg-slate-800/60 dark:hover:border-slate-600'
                }`}
              >
                <span
                  className={`mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-full border ${
                    active ? 'border-brand-600' : 'border-slate-300 dark:border-slate-600'
                  }`}
                >
                  {active ? <span className="size-2 rounded-full bg-brand-600" /> : null}
                </span>
                <span className="min-w-0">
                  <span
                    className={`block text-xs font-semibold ${
                      active ? 'text-brand-700 dark:text-brand-300' : 'text-slate-700 dark:text-slate-200'
                    }`}
                  >
                    {c.label}
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-slate-400 dark:text-slate-500">
                    {c.desc}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {/* 内容 */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <label htmlFor="fb-content" className="text-xs font-medium text-slate-600 dark:text-slate-300">
            反馈内容 <span className="text-rose-500">*</span>
          </label>
          <span
            className={`text-[11px] tabular-nums ${
              content.length > MAX_LEN ? 'text-rose-500' : 'text-slate-400 dark:text-slate-500'
            }`}
          >
            {content.length} / {MAX_LEN}
          </span>
        </div>
        <textarea
          id="fb-content"
          rows={6}
          value={content}
          maxLength={MAX_LEN}
          onChange={(e) => setContent(e.target.value)}
          placeholder="请描述遇到的问题或建议：操作路径、期望结果、当前表现（越具体越便于定位）"
          className="w-full resize-y rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        />
      </div>

      {/* 联系方式（选填） */}
      <div>
        <label htmlFor="fb-contact" className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">
          联系方式（选填）
        </label>
        <input
          id="fb-contact"
          type="text"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          placeholder="邮箱 / QQ / 微信，便于我们回复你"
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        />
      </div>

      {err ? <ErrorState error={err} onRetry={submit} /> : null}

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-slate-400 dark:text-slate-500">
          提交后可在右侧列表查看处理状态
        </span>
        <Button onClick={submit} loading={submitting} disabled={!canSubmit}>
          提交反馈
        </Button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function FeedbacksPage() {
  const [page, setPage] = useState(1)
  const size = 10
  const { data, loading, error, reload } = useApi(
    () => api.get('/feedbacks/my', { page, size }),
    [page, size],
  )
  const items = (data && data.items) || []

  return (
    <PageShell title="意见反馈" subtitle="提交问题与建议，跟进处理进度">
      <div className="grid gap-4 xl:grid-cols-5">
        {/* 左：提交表单 */}
        <div className="xl:col-span-2">
          <Card className="sticky top-20">
            <SectionHeader title="提交反馈" subtitle="带 * 为必填，内容不超过 500 字" />
            <div className="mt-3">
              <SubmitForm onSubmitted={reload} />
            </div>
          </Card>
        </div>

        {/* 右：我的反馈 */}
        <div className="xl:col-span-3">
          <Card>
            <SectionHeader
              title="我的反馈"
              subtitle={data ? `共 ${data.total} 条 · 按提交时间倒序` : '按提交时间倒序'}
              action={<TextLink to="/feedbacks/my">完整列表 →</TextLink>}
            />
            <div className="mt-3">
              <AsyncBlock
                loading={loading}
                error={error}
                empty={!items.length}
                emptyText="还没有提交过反馈"
                emptyHint="左侧选择分类并填写内容，提交后会出现在这里"
                onRetry={reload}
                loadingLabel="反馈列表加载中"
              >
                <ul className="space-y-2.5">
                  {items.map((it) => (
                    <FeedbackItem key={it.id} item={it} onDeleted={reload} />
                  ))}
                </ul>
              </AsyncBlock>
            </div>
            {items.length ? (
              <div className="mt-3">
                <Pagination page={page} pageSize={size} total={data ? data.total : 0} onChange={setPage} />
              </div>
            ) : null}
          </Card>
        </div>
      </div>
    </PageShell>
  )
}
