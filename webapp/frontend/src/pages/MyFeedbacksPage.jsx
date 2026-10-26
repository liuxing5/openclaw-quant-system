/**
 * 我的反馈（/feedbacks/my）：纯列表页，分页查看提交内容与管理员回复。
 *
 * 与 /feedbacks 共用的徽标 / 删除逻辑在这里**各自定义**（按约定不跨文件共享新模块），
 * 保证两个页面可以独立演进、独立构建。
 */
import { useState } from 'react'
import { AsyncBlock, Badge, Button, Card, ErrorState, PageShell, Pagination, SectionHeader, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 10

const CATEGORY_TEXT = {
  bug: '问题反馈',
  idea: '功能建议',
  data: '数据纠错',
  ui: '界面体验',
  other: '其他',
}

const STATUS_MAP = {
  open: { tone: 'amber', label: '待处理' },
  replied: { tone: 'emerald', label: '已回复' },
  closed: { tone: 'slate', label: '已关闭' },
  withdrawn: { tone: 'slate', label: '已撤回' },
}

function StatusBadge({ status }) {
  const s = STATUS_MAP[status] || { tone: 'slate', label: status || '未知' }
  return <Badge tone={s.tone}>{s.label}</Badge>
}

/** 契约里的删除动作是 PATCH /feedbacks/{id}，DELETE 更直观：405 时自动降级 */
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
      toast.success('已删除反馈', CATEGORY_TEXT[item.category] || '反馈已移除')
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
        <Badge tone="blue">{CATEGORY_TEXT[item.category] || item.category || '—'}</Badge>
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
      {err ? (
        <div className="mt-2">
          <ErrorState error={err} onRetry={doDelete} />
        </div>
      ) : null}
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

export default function MyFeedbacksPage() {
  const [page, setPage] = useState(1)
  const { data, loading, error, reload } = useApi(
    () => api.get('/feedbacks/my', { page, size: PAGE_SIZE }),
    [page],
  )
  const items = (data && data.items) || []

  return (
    <PageShell
      title="我的反馈"
      subtitle="提交内容、处理状态与管理员回复"
      actions={<TextLink to="/feedbacks">去提交反馈 →</TextLink>}
    >
      <Card>
        <SectionHeader
          title="反馈记录"
          subtitle={data ? `共 ${data.total} 条 · 第 ${page} 页` : `每页 ${PAGE_SIZE} 条`}
          badge={
            items.length ? (
              <Badge tone="amber">
                待处理 {items.filter((i) => i.status === 'open').length}
              </Badge>
            ) : null
          }
        />
        <div className="mt-3">
          <AsyncBlock
            loading={loading}
            error={error}
            empty={!items.length}
            emptyText="还没有提交过反馈"
            emptyHint="在「意见反馈」页选择分类并提交，这里会显示处理进度"
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
            <Pagination page={page} pageSize={PAGE_SIZE} total={data ? data.total : 0} onChange={setPage} />
          </div>
        ) : null}
      </Card>
    </PageShell>
  )
}
