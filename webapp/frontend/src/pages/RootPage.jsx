/**
 * 欢迎首页（/）：落地页。
 *
 * 为什么这么做：`/` 是进入系统的第一屏，必须在任何数据接口异常时依然可用——
 * 平台名 / 公告 / 今日指标分别包 AsyncBlock（三态独立），快捷入口磁贴不依赖接口，
 * 任何时候都能一键跳到总台、自选、策略、复盘、异动。
 */
import { Link, useNavigate } from 'react-router-dom'
import { AsyncBlock, Badge, Button, Card, SectionHeader, StatTile, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, pctTone, ratioPct } from '../lib/format.js'
import { FOOTER_NOTE } from '../lib/nav.js'

/** 情绪等级 -> 颜色（与总台保持一致：涨红跌绿的 A 股习惯） */
const LEVEL_TONE = {
  亢奋: 'text-rose-600 dark:text-rose-400',
  活跃: 'text-orange-600 dark:text-orange-400',
  温和: 'text-brand-600 dark:text-brand-400',
  低迷: 'text-amber-600 dark:text-amber-400',
  冰点: 'text-emerald-600 dark:text-emerald-400',
}

/** 快捷入口磁贴：纯前端路由，不依赖任何接口 */
const TILES = [
  { to: '/dashboard', icon: '📊', name: '总台', desc: '指数、涨跌分布与情绪一屏掌握' },
  { to: '/watchlist', icon: '⭐', name: '自选', desc: '分组管理与实时行情自选列表' },
  { to: '/strategy', icon: '🎯', name: '策略', desc: '六大量化策略扫描与命中明细' },
  { to: '/review', icon: '📝', name: '复盘', desc: '每日复盘自动生成与情绪打分' },
  { to: '/anomaly', icon: '⚡', name: '异动', desc: '急拉、放量、涨停实时监控' },
]

/** spec/pages/root.md 的原站使用指南文案 */
const GUIDES = [
  {
    title: '邮件接收',
    text: '在网页版邮箱（设置-账号-安全）生成 SMTP 授权码填入「复盘报告」配置，即可统一接收所有类型的提醒内容。',
  },
  { title: '资金流向查询', text: '跟踪主力资金动作，博弈资金追踪。' },
  { title: '严格策略执行', text: '请严格按照策略条件操作，量化评分低于 90 分视为高风险，需谨慎参与。' },
  {
    title: '定制与开发',
    text: '更多高级功能正在开发中，同时也支持根据您的需求进行专属 DIY 定制。',
  },
]

/* ---------------------------------------------------------------- 平台信息块 */

function PlatformBlock({ pub }) {
  const announcement = pub?.announcement
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="blue">{pub?.platform_name || '彪哥财经之英雄本色'}</Badge>
        {Number(pub?.marketing_total_users) > 0 ? (
          <Badge tone="slate">累计服务 {fmtNum(pub.marketing_total_users, 0)}+ 用户</Badge>
        ) : null}
        <Badge tone={pub?.register_enabled ? 'emerald' : 'amber'}>
          {pub?.register_enabled ? '开放注册' : '暂停注册'}
        </Badge>
      </div>
      {announcement ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          <span aria-hidden="true">📢</span>
          <span className="min-w-0">{announcement}</span>
        </div>
      ) : (
        <p className="text-xs text-slate-500 dark:text-slate-400">暂无公告</p>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ 今日关键指标 */

/** 上涨家数占比文案（缺数据时显示 —） */
function ratioLabel(s) {
  const up = Number(s?.up)
  const total = up + Number(s?.down || 0) + Number(s?.flat || 0)
  if (!Number.isFinite(up) || !total) return '—'
  return `${((up / total) * 100).toFixed(1)}%`
}

function TodayMetrics({ summary }) {
  const s = summary.data || {}
  const empty = !s.trade_date
  return (
    <AsyncBlock
      loading={summary.loading}
      error={summary.error}
      empty={empty}
      emptyText="今日暂无行情数据"
      emptyHint="数据底座完成当日同步后会自动展示"
      onRetry={summary.reload}
      loadingLabel="今日关键指标加载中"
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="情绪评分"
          value={`${s.score ?? '—'}`}
          sub={`${s.level || '—'} · 数据日 ${fmtDate(s.trade_date)}`}
          tone={LEVEL_TONE[s.level] || undefined}
        />
        <StatTile label="两市成交" value={fmtAmount(s.amount)} sub="沪深京合计" />
        <StatTile
          label="涨停 / 跌停"
          value={`${fmtNum(s.limit_up, 0)} / ${fmtNum(s.limit_down, 0)}`}
          sub="家数"
          tone="text-rose-600 dark:text-rose-400"
        />
        <StatTile label="封板率" value={ratioPct(s.seal_rate)} sub="触板后封住占比" />
        <StatTile
          label="上涨 / 下跌"
          value={`${fmtNum(s.up, 0)} / ${fmtNum(s.down, 0)}`}
          sub={`平盘 ${fmtNum(s.flat, 0)} 家`}
          tone={pctTone(Number(s.up) - Number(s.down))}
        />
        <StatTile label="市场宽度" value={ratioLabel(s)} sub="上涨家数占比" />
      </div>
    </AsyncBlock>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function RootPage() {
  const navigate = useNavigate()
  const pub = useApi(() => api.get('/settings/public'), [])
  const summary = useApi(() => api.get('/dashboard/summary'), [])

  return (
    <div className="fade-in-up space-y-4">
      {/* 欢迎区：平台名、公告、CTA */}
      <Card className="relative overflow-hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-16 -top-16 size-56 rounded-full bg-brand-400/15 blur-3xl dark:bg-brand-500/20"
        />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-brand-600 dark:text-brand-300">
              Welcome
            </p>
            <h2 className="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-50">欢迎使用系统</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              股票投研 · 数据一体化 · AI 辅助工具
            </p>
            <div className="mt-3">
              <AsyncBlock
                loading={pub.loading}
                error={pub.error}
                onRetry={pub.reload}
                loadingLabel="平台信息加载中"
              >
                {pub.data ? <PlatformBlock pub={pub.data} /> : null}
              </AsyncBlock>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button onClick={() => navigate('/dashboard')}>进入总台</Button>
            <Button variant="secondary" onClick={() => navigate('/strategy')}>
              开始选股
            </Button>
          </div>
        </div>

        {/* 快捷入口磁贴（不依赖接口，保证落地页永远可跳转） */}
        <div className="relative mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
          {TILES.map((t) => (
            <Link
              key={t.to}
              to={t.to}
              className="group rounded-xl border border-slate-200 bg-slate-50/70 p-3 transition hover:border-brand-400 hover:bg-white dark:border-slate-700 dark:bg-slate-800/50 dark:hover:border-brand-500/60 dark:hover:bg-slate-800"
            >
              <div className="flex items-center gap-2">
                <span className="text-lg" aria-hidden="true">
                  {t.icon}
                </span>
                <span className="text-sm font-semibold text-slate-800 group-hover:text-brand-600 dark:text-slate-100 dark:group-hover:text-brand-300">
                  {t.name}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{t.desc}</p>
            </Link>
          ))}
        </div>
      </Card>

      {/* 今日关键指标 */}
      <Card>
        <SectionHeader
          title="今日关键指标"
          subtitle="来自总台轮询接口，交易时段随行情更新"
          action={<TextLink to="/dashboard">进入总台 →</TextLink>}
        />
        <div className="mt-3">
          <TodayMetrics summary={summary} />
        </div>
      </Card>

      {/* 使用指南 */}
      <Card>
        <SectionHeader title="使用指南" subtitle="上手前建议先读一遍，能避开大部分操作误区" />
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {GUIDES.map((g, i) => (
            <div
              key={g.title}
              className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/50"
            >
              <p className="flex items-center gap-2 text-xs font-semibold text-slate-800 dark:text-slate-100">
                <span className="grid size-5 shrink-0 place-items-center rounded-md bg-brand-600 text-[11px] font-bold text-white">
                  {i + 1}
                </span>
                {g.title}
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{g.text}</p>
            </div>
          ))}
        </div>
      </Card>

      {/* 免责声明 */}
      <Card>
        <SectionHeader title="免责声明" subtitle="投资有风险，入市需谨慎" />
        <p className="mt-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{FOOTER_NOTE}</p>
      </Card>
    </div>
  )
}
