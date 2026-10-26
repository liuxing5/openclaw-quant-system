/**
 * 金融书单（/academy/books）：按主题分组的中文荐读书目。
 *
 * 本地没有专门的书单接口，因此书目在页内硬编码——这页的价值是「选书 + 排序 + 一句话看点」，
 * 属于编辑性内容，不该依赖后端数据。每本都给出作者、核心看点、适用读者、主题标签与阅读顺序。
 */
import { Link } from 'react-router-dom'
import { Badge, Card, PageShell, SectionHeader, StatTile, TextLink } from '../components/ui.jsx'

const THEMES = [
  {
    key: 'trading',
    title: '股票交易',
    subtitle: '建立交易底层认知',
    desc: '从市场心理、安全边际到长期持有，搭建不依赖运气的投资框架。',
    tone: 'border-rose-200 bg-rose-50/60 dark:border-rose-500/30 dark:bg-rose-500/10',
    tagTone: 'rose',
  },
  {
    key: 'index',
    title: 'ETF 与指数',
    subtitle: '用低成本拥抱市场',
    desc: '理解指数、费率、资产配置与再平衡，把长期投资变成可执行的系统。',
    tone: 'border-brand-200 bg-brand-50/60 dark:border-brand-500/30 dark:bg-brand-500/10',
    tagTone: 'blue',
  },
  {
    key: 'macro',
    title: '黄金现货与宏观',
    subtitle: '读懂货币与周期',
    desc: '从黄金历史、债务周期与反身性出发，理解避险资产和宏观环境。',
    tone: 'border-amber-200 bg-amber-50/60 dark:border-amber-500/30 dark:bg-amber-500/10',
    tagTone: 'amber',
  },
]

const BOOKS = [
  {
    order: 1,
    theme: 'trading',
    title: '股票大作手回忆录',
    en: 'Reminiscences of a Stock Operator',
    author: '埃德温·勒菲弗',
    pitch: '市场会重复人性：顺势、等待关键点、控制亏损，比频繁预测更重要。',
    fit: '希望建立交易心理与趋势思维的个人投资者',
    tags: ['交易心理', '趋势', '风控'],
    stage: '入门',
  },
  {
    order: 2,
    theme: 'trading',
    title: '聪明的投资者',
    en: 'The Intelligent Investor',
    author: '本杰明·格雷厄姆',
    pitch: '用安全边际区分投资与投机，让价格波动成为机会而不是情绪来源。',
    fit: '偏价值投资、希望构建长期防守体系的读者',
    tags: ['价值投资', '安全边际', '长期主义'],
    stage: '入门',
  },
  {
    order: 3,
    theme: 'trading',
    title: '彼得·林奇的成功投资',
    en: 'One Up on Wall Street',
    author: '彼得·林奇',
    pitch: '从生活中发现公司，再用基本面验证；投资机会常藏在你熟悉的行业里。',
    fit: '愿意研究公司、寻找成长股的普通投资者',
    tags: ['成长股', '公司研究', '生活观察'],
    stage: '进阶',
  },
  {
    order: 4,
    theme: 'index',
    title: '共同基金常识',
    en: 'Common Sense on Mutual Funds',
    author: '约翰·博格',
    pitch: '费用、税负与长期复利决定最终结果，低成本指数基金是普通人可复制的优势。',
    fit: '希望用基金做长期资产配置的投资者',
    tags: ['指数基金', '费率', '复利'],
    stage: '入门',
  },
  {
    order: 5,
    theme: 'index',
    title: '指数基金投资指南',
    en: 'Index Fund Investment Guide',
    author: '银行螺丝钉',
    pitch: '把估值、定投与止盈规则落到具体操作，适合作为指数基金的入门手册。',
    fit: '刚开始定投指数基金、需要操作框架的读者',
    tags: ['定投', '估值', '实操'],
    stage: '入门',
  },
  {
    order: 6,
    theme: 'index',
    title: '赢得输家的游戏',
    en: "Winning the Loser's Game",
    author: '查尔斯·埃利斯',
    pitch: '当市场越来越专业，少犯错比追求神操作更重要，低成本长期持有更具优势。',
    fit: '希望减少无效交易、转向长期配置的投资者',
    tags: ['长期投资', '低费用', '行为偏差'],
    stage: '进阶',
  },
  {
    order: 7,
    theme: 'macro',
    title: '黄金简史',
    en: 'The Power of Gold',
    author: '彼得·L·伯恩斯坦',
    pitch: '从货币史理解黄金的信用属性、避险需求与价格叙事，而不是只看短期涨跌。',
    fit: '想理解黄金货币属性与历史脉络的读者',
    tags: ['黄金', '货币史', '避险'],
    stage: '进阶',
  },
  {
    order: 8,
    theme: 'macro',
    title: '债务危机',
    en: 'Principles for Navigating Big Debt Crises',
    author: '瑞·达利欧',
    pitch: '用债务周期框架理解去杠杆、货币政策与资产价格之间的传导关系。',
    fit: '关注宏观经济、债务周期与系统性风险的读者',
    tags: ['债务周期', '去杠杆', '宏观'],
    stage: '进阶',
  },
  {
    order: 9,
    theme: 'macro',
    title: '金融炼金术',
    en: 'The Alchemy of Finance',
    author: '乔治·索罗斯',
    pitch: '用反身性解释认知与价格如何相互强化，提醒投资者警惕看似稳定的市场叙事。',
    fit: '希望深入理解市场预期与宏观交易的进阶读者',
    tags: ['反身性', '预期', '宏观交易'],
    stage: '进阶',
  },
]

function BookCard({ book, tagTone }) {
  return (
    <article className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-slate-900 text-sm font-bold tabular-nums text-white dark:bg-slate-100 dark:text-slate-900">
          {book.order}
        </span>
        <Badge tone={book.stage === '入门' ? 'emerald' : 'blue'}>{book.stage}</Badge>
      </div>

      <h3 className="mt-3 text-base font-semibold leading-6 text-slate-900 dark:text-slate-100">{book.title}</h3>
      <p className="mt-0.5 text-[11px] italic text-slate-400 dark:text-slate-500">{book.en}</p>
      <p className="mt-1 text-xs font-medium text-brand-600 dark:text-brand-400">{book.author}</p>

      <p className="mt-2.5 flex-1 text-sm leading-6 text-slate-600 dark:text-slate-300">{book.pitch}</p>

      <p className="mt-3 text-[11px] leading-5 text-slate-500 dark:text-slate-400">适合：{book.fit}</p>

      <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-slate-100 pt-2.5 dark:border-slate-800">
        {book.tags.map((t) => (
          <span
            key={t}
            className={`rounded-md border px-2 py-0.5 text-[11px] ${
              tagTone === 'rose'
                ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300'
                : tagTone === 'amber'
                  ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300'
                  : 'border-brand-200 bg-brand-50 text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-300'
            }`}
          >
            {t}
          </span>
        ))}
      </div>
    </article>
  )
}

export default function AcademyBooksPage() {
  return (
    <PageShell
      title="投资学堂 · 金融书单"
      subtitle="把经典读成自己的投资操作系统"
      actions={<TextLink to="/academy">← 返回学堂首页</TextLink>}
    >
      <Card>
        <p className="text-sm leading-7 text-slate-600 dark:text-slate-300">
          从交易心理、指数化投资到黄金与宏观周期，精选 {BOOKS.length} 本经得起时间检验的经典。
          每本书只保留一句核心看点，方便按需精读；标签与「适合」一栏告诉你它该在什么阶段读。
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatTile label="经典书目" value={BOOKS.length} sub="逐本精挑" />
          <StatTile label="阅读主题" value={THEMES.length} sub="交易 / 指数 / 宏观" />
          <StatTile label="建议顺序" value={`1 - ${BOOKS.length}`} sub="由浅入深" />
        </div>
      </Card>

      {THEMES.map((theme) => {
        const books = BOOKS.filter((b) => b.theme === theme.key)
        return (
          <Card key={theme.key}>
            <SectionHeader
              title={theme.title}
              subtitle={`${theme.subtitle} · ${books.length} 本`}
              badge={<Badge tone={theme.tagTone}>{theme.title}</Badge>}
            />
            <p className="mt-2 text-xs leading-6 text-slate-500 dark:text-slate-400">{theme.desc}</p>
            <div className={`mt-3 rounded-lg border p-3 ${theme.tone}`}>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {books.map((b) => (
                  <BookCard key={b.order} book={b} tagTone={theme.tagTone} />
                ))}
              </div>
            </div>
          </Card>
        )
      })}

      <Card>
        <SectionHeader title="建议阅读顺序" subtitle="按这个顺序读，前后内容才会互相咬合" />
        <p className="mt-2 text-sm leading-7 text-slate-600 dark:text-slate-300">
          先读《聪明的投资者》建立风险意识，再用《共同基金常识》理解低成本指数化；
          接着进入《股票大作手回忆录》校准交易心理，然后用《赢得输家的游戏》收敛交易频率；
          最后以《债务危机》与《黄金简史》补齐宏观视角，《金融炼金术》留给已经能独立形成观点的你。
        </p>
        <ul className="mt-3 grid gap-2 sm:grid-cols-3">
          {[
            { t: '经典常读常新', d: '同一本书在不同仓位状态下重读，结论会完全不同。' },
            { t: '带着问题阅读', d: '先写下当前卡住的问题，只记录能回答它的段落。' },
            { t: '记录可执行结论', d: '每本书合上时留一条能立刻改掉的交易习惯。' },
          ].map((s) => (
            <li
              key={s.t}
              className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/50"
            >
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{s.t}</p>
              <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{s.d}</p>
            </li>
          ))}
        </ul>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-slate-400 dark:text-slate-500">
          <span>
            想先补盘面基础？<Link to="/academy/basics" className="font-medium text-brand-600 hover:underline dark:text-brand-400">股票基础知识 →</Link>
          </span>
          <span>风险提示：书单仅作阅读推荐，不构成任何投资建议。</span>
        </div>
      </Card>
    </PageShell>
  )
}
