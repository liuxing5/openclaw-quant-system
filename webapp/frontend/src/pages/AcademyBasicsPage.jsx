/**
 * 股票基础知识（/academy/basics）：长文阅读页。
 *
 * 排版按「读长文」来设计：正文限宽 max-w-3xl、行高 leading-7、分节留白；
 * 左侧目录用锚点平滑滚动，并随滚动高亮当前章节——长文最容易迷失的就是位置感。
 * 数据以契约的 sections[] 为准；若后端给的是 markdown 全文，则在页内解析成同构分节，
 * 保证两种口径都能渲染出目录与正文。
 */
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AsyncBlock, Badge, Card, EmptyState, PageShell, Select, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDate } from '../lib/format.js'

const CATEGORY_LABEL = {
  basics: '股票基础',
  kline: 'K 线形态',
  volume: '量价关系',
  indicator: '技术指标',
  risk: '仓位与风控',
  books: '金融书单',
}

/** markdown 兜底解析：## 分节、| 表格、- 列表，产出与契约同构的 sections */
function parseMarkdown(md) {
  const sections = []
  let cur = null
  let rows = null
  const ensure = () => {
    if (!cur) {
      cur = { heading: '正文', paras: [] }
      sections.push(cur)
    }
    return cur
  }
  const flushTable = () => {
    if (rows && rows.length) ensure().paras.push({ type: 'table', rows })
    rows = null
  }

  String(md || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .forEach((raw) => {
      const line = raw.trim()
      if (!line) {
        flushTable()
        return
      }
      if (line.startsWith('|')) {
        const cells = line
          .split('|')
          .slice(1, -1)
          .map((c) => c.trim())
        if (cells.every((c) => /^:?-{2,}:?$/.test(c))) return // 表头分隔行
        rows = rows || []
        rows.push(cells)
        return
      }
      const h2 = line.match(/^##\s+(.*)$/)
      if (h2) {
        flushTable()
        cur = { heading: h2[1].trim(), paras: [] }
        sections.push(cur)
        return
      }
      if (/^#\s+/.test(line)) {
        flushTable() // 一级标题与文章标题重复，忽略
        return
      }
      flushTable()
      ensure().paras.push(line)
    })
  flushTable()
  return sections
}

function sectionsOf(article) {
  if (!article) return []
  if (Array.isArray(article.sections) && article.sections.length) return article.sections
  const md = article.markdown || article.content || article.body
  return typeof md === 'string' ? parseMarkdown(md) : []
}

/** 行内 **加粗** 渲染，避免正文里出现裸星号 */
function RichText({ text }) {
  const parts = String(text).split(/\*\*(.+?)\*\*/g)
  return (
    <>
      {parts.map((p, i) =>
        i % 2 ? (
          <strong key={i} className="font-semibold text-slate-800 dark:text-slate-100">
            {p}
          </strong>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  )
}

function Para({ para }) {
  if (para && typeof para === 'object' && para.type === 'table') {
    const [head, ...body] = para.rows
    return (
      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-50 text-left text-slate-500 dark:bg-slate-800/70 dark:text-slate-400">
              {head.map((c, i) => (
                <th key={i} className="px-3 py-2 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri} className="border-t border-slate-100 dark:border-slate-800">
                {row.map((c, ci) => (
                  <td key={ci} className="px-3 py-2 text-slate-600 dark:text-slate-300">
                    <RichText text={c} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const text = typeof para === 'object' && para ? String(para.text || para.content || '') : String(para)
  if (text.startsWith('> ')) {
    return (
      <p className="border-l-2 border-brand-400 pl-3 text-slate-600 dark:border-brand-500/60 dark:text-slate-300">
        <RichText text={text.slice(2)} />
      </p>
    )
  }
  if (/^([-*]|\d+\.)\s+/.test(text)) {
    return (
      <div className="flex gap-2.5">
        <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-brand-400" aria-hidden="true" />
        <p className="flex-1 text-slate-600 dark:text-slate-300">
          <RichText text={text.replace(/^([-*]|\d+\.)\s+/, '')} />
        </p>
      </div>
    )
  }
  return (
    <p className="text-slate-600 dark:text-slate-300">
      <RichText text={text} />
    </p>
  )
}

export default function AcademyBasicsPage() {
  const [params, setParams] = useSearchParams()
  const [active, setActive] = useState(0)

  const { data, loading, error, reload } = useApi(() => api.get('/academy/articles'), [])
  const articles = data?.items || []
  const currentId = params.get('id') || ''
  const article = articles.find((a) => String(a.id) === currentId) || articles[0] || null
  const sections = useMemo(() => sectionsOf(article), [article])

  // 首次进入或 ?id= 缺失时补上第一篇，保证 URL 与选中态一致
  useEffect(() => {
    if (!data || articles.length === 0) return
    if (!currentId && articles[0]) setParams({ id: String(articles[0].id) }, { replace: true })
  }, [data, currentId, articles, setParams])

  // 切换文章回到页首，避免停留在上一篇的滚动位置
  useEffect(() => {
    window.scrollTo({ top: 0 })
    setActive(0)
  }, [currentId])

  // 滚动高亮：取最后一个「顶部已越过参考线」的章节
  useEffect(() => {
    if (!sections.length) return undefined
    const onScroll = () => {
      let idx = 0
      for (let i = 0; i < sections.length; i += 1) {
        const el = document.getElementById(`sec-${i}`)
        if (el && el.getBoundingClientRect().top <= 96) idx = i
      }
      setActive(idx)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => window.removeEventListener('scroll', onScroll)
  }, [sections])

  const goTo = (i) => {
    const el = document.getElementById(`sec-${i}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setActive(i)
  }

  const switcher =
    articles.length > 0 && articles.length <= 6 ? (
      <Tabs
        tabs={articles.map((a) => ({ value: String(a.id), label: a.title }))}
        value={article ? String(article.id) : ''}
        onChange={(v) => setParams({ id: v })}
      />
    ) : (
      <Select
        className="min-w-56"
        aria-label="选择文章"
        value={article ? String(article.id) : ''}
        options={articles.map((a) => ({ value: String(a.id), label: a.title }))}
        onChange={(v) => setParams({ id: v })}
      />
    )

  return (
    <PageShell
      title="投资学堂 · 股票基础知识"
      subtitle="从看懂盘面，到建立自己的判断框架：每个词条都给出定义、观察方法、失效条件与风险提醒。"
      actions={switcher}
    >
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={reload}
        empty={!loading && !error && articles.length === 0}
        emptyText="文章库暂未就绪"
        emptyHint="文章入库后可在此阅读，也可先看看金融书单"
        loadingLabel="课程加载中…"
      >
        {!article ? (
          <EmptyState text="请选择一篇文章" />
        ) : (
          <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            {/* 左：目录 */}
            <aside className="h-fit lg:sticky lg:top-20">
              <Card className="p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  本篇目录
                </p>
                <nav className="mt-2 space-y-0.5">
                  {sections.length === 0 ? (
                    <p className="text-xs text-slate-400">暂无章节</p>
                  ) : (
                    sections.map((s, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => goTo(i)}
                        className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-xs transition ${
                          i === active
                            ? 'bg-brand-600 text-white shadow-sm'
                            : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800/70'
                        }`}
                        title={s.heading}
                      >
                        {s.heading || `第 ${i + 1} 节`}
                      </button>
                    ))
                  )}
                </nav>
              </Card>
            </aside>

            {/* 右：正文 */}
            <article className="min-w-0 max-w-3xl">
              <Card>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="blue">{CATEGORY_LABEL[article.category] || article.category || '基础知识'}</Badge>
                  {article.level ? <Badge tone="slate">{article.level}</Badge> : null}
                  <Badge tone="amber">约 {article.reading_minutes ?? article.reading_time ?? '—'} 分钟阅读</Badge>
                  {article.updated_at ? (
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">
                      更新于 {fmtDate(article.updated_at)}
                    </span>
                  ) : null}
                </div>
                <h1 className="mt-2 text-xl font-semibold leading-8 text-slate-900 dark:text-slate-100">
                  {article.title}
                </h1>
                {article.summary ? (
                  <p className="mt-2 border-l-2 border-brand-500 pl-3 text-sm leading-6 text-slate-600 dark:border-brand-400 dark:text-slate-300">
                    {article.summary}
                  </p>
                ) : null}
              </Card>

              {sections.length === 0 ? (
                <Card className="mt-4">
                  <EmptyState text="该文章暂无正文" hint="请返回选择其他文章" />
                </Card>
              ) : (
                <div className="mt-4 space-y-6">
                  {sections.map((s, i) => (
                    <section key={i} id={`sec-${i}`} className="scroll-mt-20">
                      <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900 dark:text-slate-100">
                        <span className="h-4 w-1 rounded-full bg-brand-500" aria-hidden="true" />
                        {s.heading || `第 ${i + 1} 节`}
                      </h2>
                      <div className="mt-3 space-y-3 pl-3 text-sm leading-7">
                        {(s.paras || []).map((p, pi) => (
                          <Para key={pi} para={p} />
                        ))}
                      </div>
                    </section>
                  ))}

                  <p className="border-t border-slate-100 pt-4 text-[11px] leading-5 text-slate-400 dark:border-slate-800 dark:text-slate-500">
                    风险提示：本文仅用于方法学习，不构成投资建议；任何结论都要结合仓位管理、止损纪律与当时的市场环境。
                  </p>
                </div>
              )}
            </article>
          </div>
        )}
      </AsyncBlock>
    </PageShell>
  )
}
