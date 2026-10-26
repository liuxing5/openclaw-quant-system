/**
 * 投资学堂首页（/academy）：学院导航 + 精选文章。
 *
 * 原站该路由未注册（404），本地已挂载，因此落地为「入口分栏 + 精选文章」的导航页：
 * 两个入口分别通往长文精读页与书单页，文章列表直接取 /academy/articles 的前若干篇，
 * 点击进入基础知识页并带上 ?id= 定位到该篇。
 */
import { Link } from 'react-router-dom'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'

const PICK_COUNT = 6

const CATEGORY_LABEL = {
  basics: '股票基础',
  kline: 'K 线形态',
  volume: '量价关系',
  indicator: '技术指标',
  risk: '仓位与风控',
  books: '金融书单',
}

const ENTRIES = [
  {
    to: '/academy/basics',
    title: '股票基础知识',
    desc: '从集合竞价、分时图到换手率与估值指标，每个词条都给出定义、观察方法、失效条件与风险提醒。',
    meta: '长文精读 · 左侧目录导航',
    tone: 'from-brand-500/15 to-brand-500/5 border-brand-200 dark:border-brand-500/30',
  },
  {
    to: '/academy/books',
    title: '金融书单',
    desc: '把经典读成自己的投资操作系统：按交易心理、指数化投资、黄金与宏观周期三类主题排序荐读。',
    meta: '9 本经典 · 含阅读顺序',
    tone: 'from-amber-500/15 to-amber-500/5 border-amber-200 dark:border-amber-500/30',
  },
]

export default function AcademyPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/academy/articles'), [])
  const articles = (data?.items || []).slice(0, PICK_COUNT)
  const total = data?.total ?? (data?.items || []).length
  const categories = new Set((data?.items || []).map((a) => a.category).filter(Boolean))

  return (
    <PageShell
      title="投资学堂"
      subtitle="先理解价格、成交量与市场情绪之间的关系，再谈策略；这里沉淀基础知识与值得反复读的经典。"
    >
      {/* 入口磁贴 */}
      <div className="grid gap-4 md:grid-cols-2">
        {ENTRIES.map((e) => (
          <Link
            key={e.to}
            to={e.to}
            className={`group rounded-xl border bg-gradient-to-br p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md ${e.tone}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">{e.title}</h2>
                <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">{e.desc}</p>
                <p className="mt-3 text-[11px] text-slate-500 dark:text-slate-400">{e.meta}</p>
              </div>
              <span className="shrink-0 text-lg text-slate-400 transition group-hover:translate-x-0.5 group-hover:text-brand-500">
                →
              </span>
            </div>
          </Link>
        ))}
      </div>

      {/* 精选文章 */}
      <Card>
        <SectionHeader
          title="精选文章"
          subtitle="来自学院文章库，点击进入基础知识页阅读全文"
          badge={<Badge tone="blue">{total ? `${total} 篇` : '—'}</Badge>}
          action={<TextLink to="/academy/basics">全部文章 →</TextLink>}
        />

        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          empty={!loading && !error && articles.length === 0}
          emptyText="文章库暂未就绪"
          emptyHint="文章入库后会自动出现在这里，可先阅读金融书单"
          loadingLabel="文章加载中…"
        >
          <ul className="mt-3 grid gap-3 md:grid-cols-2">
            {articles.map((a, i) => (
              <li key={a.id || a.slug || i}>
                <Link
                  to={`/academy/basics?id=${encodeURIComponent(a.id || a.slug || '')}`}
                  className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-brand-400 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-brand-500/60"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="slate">{CATEGORY_LABEL[a.category] || a.category || '文章'}</Badge>
                    {a.level ? <Badge tone="blue">{a.level}</Badge> : null}
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">
                      约 {a.reading_minutes ?? a.reading_time ?? '—'} 分钟
                    </span>
                  </div>
                  <h3 className="mt-2 text-sm font-semibold leading-6 text-slate-800 dark:text-slate-100">
                    {a.title}
                  </h3>
                  {a.summary ? (
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">
                      {a.summary}
                    </p>
                  ) : null}
                  <span className="mt-auto pt-2 text-xs font-medium text-brand-600 dark:text-brand-400">
                    阅读全文 →
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </AsyncBlock>
      </Card>

      {/* 学习方法说明：给首页一点“怎么用”的真实文案 */}
      <Card>
        <SectionHeader title="怎么学更有效" subtitle="三步走，避免把知识点学成孤立的名词" />
        <ol className="mt-3 grid gap-3 sm:grid-cols-3">
          {[
            { n: '01', t: '先看环境', d: '指数、量能、涨停效应决定当天值不值得重仓出手。' },
            { n: '02', t: '再定框架', d: '用知识词条里的「观察方法 + 失效条件」形成自己的判断顺序。' },
            { n: '03', t: '最后复盘', d: '把结论写进每日复盘，用真实结果校正认知，而不是靠感觉。' },
          ].map((s) => (
            <li key={s.n} className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/50">
              <p className="text-xs font-bold tabular-nums text-brand-600 dark:text-brand-400">{s.n}</p>
              <p className="mt-1 text-sm font-semibold text-slate-800 dark:text-slate-100">{s.t}</p>
              <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{s.d}</p>
            </li>
          ))}
        </ol>
        <p className="mt-3 text-[11px] leading-5 text-slate-400 dark:text-slate-500">
          风险提示：学堂内容仅用于知识普及，不构成任何投资建议；所有方法都要配合仓位管理与止损纪律。
        </p>
      </Card>
    </PageShell>
  )
}
