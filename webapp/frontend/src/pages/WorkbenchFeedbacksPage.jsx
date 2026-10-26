/**
 * /workbench/feedbacks 反馈管理：全部反馈列表 + 行内回复。
 *
 * 写接口 PATCH /workbench/feedbacks/{id} body {status:"replied", admin_reply}：
 * 发送中按钮 loading，成功 toast + reload()，失败 toast 且面板保持打开可直接重试。
 * 列表分页走 Pagination，空 / 错误 / 加载三态统一由 AsyncBlock 兜底。
 */
import React, { useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, Pagination, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 20
const arr = (v) => (Array.isArray(v) ? v : [])

function pick(obj, keys, fallback = null) {
  if (!obj) return fallback
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return fallback
}

const CATEGORY_TONE = { bug: 'rose', 缺陷: 'rose', feature: 'blue', 功能: 'blue', suggest: 'amber', 建议: 'amber', question: 'blue', 咨询: 'blue', other: 'slate', 其他: 'slate' }
const CATEGORY_LABEL = { bug: '缺陷', feature: '功能', suggest: '建议', question: '咨询', other: '其他' }

const STATUS_TONE = { open: 'amber', pending: 'amber', replied: 'blue', resolved: 'emerald', closed: 'slate' }
const STATUS_TEXT = { open: '待处理', pending: '待处理', replied: '已回复', resolved: '已解决', closed: '已关闭' }

function MetaBadges({ meta }) {
  if (!meta) return null
  const badges = []
  if (meta.local) badges.push(<Badge key="local" tone="blue">本地单用户</Badge>)
  if (meta.seed) badges.push(<Badge key="seed" tone="amber">种子演示数据</Badge>)
  if (meta.degraded) badges.push(<Badge key="degraded" tone="rose">降级数据</Badge>)
  if (meta.source) badges.push(<Badge key="src" tone="slate">来源 {String(meta.source)}</Badge>)
  if (!badges.length) return null
  return <span className="inline-flex flex-wrap items-center gap-1.5">{badges}</span>
}

export default function WorkbenchFeedbacksPage() {
  const [page, setPage] = useState(1)
  const [openId, setOpenId] = useState(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(null)
  const [sendError, setSendError] = useState(null)

  const { data, loading, error, reload } = useApi(
    () => api.get('/workbench/feedbacks', { page, size: PAGE_SIZE }),
    [page],
  )

  const items = arr(data?.items)
  const total = Number(data?.total ?? items.length) || 0
  const openCount = items.filter((f) => ['open', 'pending'].includes(String(pick(f, ['status'], '')).toLowerCase())).length

  const toggleRow = (fb, rowKey) => {
    const id = pick(fb, ['id', 'feedback_id'], rowKey)
    if (id === null || id === undefined) return
    if (openId === id) {
      setOpenId(null)
      setSendError(null)
      return
    }
    setOpenId(id)
    setSendError(null)
    // 打开时预填已有回复，避免覆盖管理员之前的答复
    setDraft(String(pick(fb, ['admin_reply', 'reply'], '') || ''))
  }

  const submitReply = async (fb, rowKey) => {
    const id = pick(fb, ['id', 'feedback_id'], rowKey)
    const text = draft.trim()
    if (!id) return
    if (!text) {
      setSendError('回复内容不能为空')
      toast.error('无法提交', '请先填写回复内容')
      return
    }
    setSending(id)
    setSendError(null)
    try {
      await api.patch(`/workbench/feedbacks/${id}`, { status: 'replied', admin_reply: text })
      toast.success('回复已提交', '反馈状态已更新为「已回复」')
      setOpenId(null)
      setDraft('')
      reload()
    } catch (e) {
      // 失败保留面板与草稿，用户改完可直接重试
      setSendError(e.message || '提交失败，请稍后重试')
      toast.error('回复失败', `${e.message || '请求失败'}，面板已保留，可直接重试`)
    } finally {
      setSending(null)
    }
  }

  const cols = 6

  return (
    <PageShell
      title="反馈管理"
      subtitle="全部用户反馈 · 点击行展开可内联回复"
      actions={
        <div className="flex items-center gap-2">
          <MetaBadges meta={data?.meta} />
          <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
            刷新
          </Button>
        </div>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={reload}
        loadingLabel="反馈列表加载中"
        empty={!loading && !error && items.length === 0}
        emptyText="暂无反馈"
        emptyHint="用户在「意见反馈」提交后会出现在这里"
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card className="p-3">
              <p className="text-[11px] text-slate-500 dark:text-slate-400">反馈总数</p>
              <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{total}</p>
            </Card>
            <Card className="p-3">
              <p className="text-[11px] text-slate-500 dark:text-slate-400">本页待处理</p>
              <p className={`mt-1 text-xl font-semibold tabular-nums ${openCount ? 'text-amber-600 dark:text-amber-400' : 'text-slate-900 dark:text-slate-100'}`}>
                {openCount}
              </p>
            </Card>
            <Card className="p-3">
              <p className="text-[11px] text-slate-500 dark:text-slate-400">本页已回复</p>
              <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                {items.filter((f) => String(pick(f, ['status'], '')).toLowerCase() === 'replied').length}
              </p>
            </Card>
            <Card className="p-3">
              <p className="text-[11px] text-slate-500 dark:text-slate-400">数据来源</p>
              <div className="mt-1.5">
                <MetaBadges meta={data?.meta} />
                {!data?.meta ? <span className="text-xs text-slate-400">接口未标记</span> : null}
              </div>
            </Card>
          </div>

          <Card>
            <SectionHeader
              title="反馈列表"
              subtitle={`共 ${total} 条 · 每页 ${PAGE_SIZE} 条 · 点击行展开回复框`}
              badge={<MetaBadges meta={data?.meta} />}
            />
            <div className="mt-3 overflow-x-auto thin-scroll">
              <table className="w-full min-w-[860px] text-xs">
                <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">分类</th>
                    <th className="pb-1.5 font-medium">内容</th>
                    <th className="pb-1.5 font-medium">联系方式</th>
                    <th className="pb-1.5 font-medium">状态</th>
                    <th className="pb-1.5 font-medium">提交时间</th>
                    <th className="pb-1.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((fb, i) => {
                    const id = pick(fb, ['id', 'feedback_id'], `fb-${i}`)
                    const cat = String(pick(fb, ['category', 'type'], 'other')).toLowerCase()
                    const status = String(pick(fb, ['status'], 'open')).toLowerCase()
                    const expanded = openId === id
                    const reply = pick(fb, ['admin_reply', 'reply'], null)
                    return (
                      <React.Fragment key={id}>
                        <tr
                          onClick={() => toggleRow(fb, id)}
                          className={`cursor-pointer border-t border-slate-100 dark:border-slate-800 ${
                            expanded ? 'bg-brand-50/60 dark:bg-brand-500/10' : 'hover:bg-slate-50 dark:hover:bg-slate-800/60'
                          }`}
                        >
                          <td className="py-2">
                            <Badge tone={CATEGORY_TONE[cat] || 'slate'}>{CATEGORY_LABEL[cat] || cat}</Badge>
                          </td>
                          <td className="max-w-[24rem] py-2">
                            <p className="truncate text-slate-700 dark:text-slate-200">
                              {pick(fb, ['content', 'message', 'body'], '—')}
                            </p>
                            {reply ? (
                              <p className="mt-0.5 truncate text-[10px] text-brand-600 dark:text-brand-400">
                                已回复：{reply}
                              </p>
                            ) : null}
                          </td>
                          <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                            {pick(fb, ['contact', 'email', 'phone'], '—')}
                          </td>
                          <td className="py-2">
                            <Badge tone={STATUS_TONE[status] || 'slate'}>{STATUS_TEXT[status] || status}</Badge>
                          </td>
                          <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                            {fmtDateTime(pick(fb, ['created_at', 'submit_at', 'at']))}
                          </td>
                          <td className="py-2 text-right">
                            <span className="text-[11px] font-medium text-brand-600 dark:text-brand-400">
                              {expanded ? '收起 ↑' : '回复 ↓'}
                            </span>
                          </td>
                        </tr>
                        {expanded ? (
                          <tr className="border-t border-brand-100 bg-brand-50/40 dark:border-brand-500/20 dark:bg-brand-500/5">
                            <td colSpan={cols} className="px-2 py-3" onClick={(e) => e.stopPropagation()}>
                              <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400" htmlFor={`reply-${id}`}>
                                回复内容（提交后状态置为「已回复」）
                              </label>
                              <textarea
                                id={`reply-${id}`}
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                rows={3}
                                placeholder="输入给用户的答复…"
                                className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                              />
                              {sendError ? (
                                <p className="mt-1 text-[11px] text-rose-600 dark:text-rose-400">{sendError}</p>
                              ) : null}
                              <div className="mt-2 flex items-center justify-end gap-2">
                                <Button variant="secondary" size="sm" onClick={() => toggleRow(fb, id)} disabled={Boolean(sending)}>
                                  取消
                                </Button>
                                <Button
                                  size="sm"
                                  loading={sending === id}
                                  onClick={() => submitReply(fb, id)}
                                >
                                  提交回复
                                </Button>
                              </div>
                            </td>
                          </tr>
                        ) : null}
                      </React.Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
          </Card>
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
