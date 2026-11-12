/** 侧边导航：按“找方向 → 看行情 → 做研究 → 管交易 → 查数据”组织。 */
export const NAV = [
  {
    group: '洞察',
    items: [
      { label: '今日推荐', to: '/recommend' },
      { label: '实时资金', to: '/anomaly' },
    ],
  },
  {
    group: '行情',
    items: [
      { label: '市场总览', to: '/dashboard' },
      { label: '板块行情', to: '/concept-analysis' },
      { label: '行业板块', to: '/industry-analysis' },
      { label: '全球行情', to: '/global-market' },
      { label: '指数总览', to: '/index' },
    ],
  },
  {
    group: '研究',
    items: [
      { label: '个股分析', to: '/stock-analysis' },
      { label: 'AI策略技能', to: '/ai/skills' },
      { label: '选股策略', to: '/strategy' },
      { label: '游资龙虎榜', to: '/dragon-tiger' },
    ],
  },
  {
    group: '交易',
    items: [
      { label: '持仓账户', to: '/holdings' },
      { label: '模拟账户', to: '/simulation/account' },
      { label: '每日复盘', to: '/review' },
    ],
  },
  {
    group: '数据',
    items: [
      { label: '行情数据', to: '/data/quotes' },
      { label: '财务数据', to: '/finance/statements' },
      { label: '市场环境', to: '/market-env' },
    ],
  },
  {
    group: '更多',
    items: [
      { label: '全部功能', to: '/more' },
      { label: '每日快讯', to: '/info/news-flash' },
      { label: '全网热搜', to: '/info/hot-search' },
      { label: '并购重组', to: '/merger-restructuring' },
      { label: '个人中心', to: '/profile' },
    ],
  },
]

export const FOOTER_NOTE =
  '本平台仅提供数据辅助分析，不提供保本理财及代客操盘服务，投资决策需由您独立作出。'
