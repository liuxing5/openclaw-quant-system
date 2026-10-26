/** 侧边导航配置：与站点信息架构一一对应，页面可按此顺序落地。 */
export const NAV = [
  {
    group: '精选',
    items: [
      { label: '荐股', to: '/recommend' },
    ],
  },
  {
    group: '行情',
    items: [
      { label: '总台', to: '/dashboard' },
      { label: '连板排行', to: '/limit-ladder' },
      { label: '概念板块', to: '/concept-analysis' },
      { label: '行业板块', to: '/industry-analysis' },
      { label: '外围指数', to: '/global-index' },
      { label: '油和金', to: '/oil-gold' },
    ],
  },
  {
    group: '投研',
    items: [
      { label: '个股分析', to: '/stock-analysis' },
      { label: '选股', to: '/strategy' },
      { label: '游资龙虎榜', to: '/dragon-tiger' },
      { label: '资金流向', to: '/info/fund-flow' },
      { label: '市场环境', to: '/market-env' },
    ],
  },
  {
    group: '交易',
    items: [
      { label: 'AI 持仓管家', to: '/watchlist' },
      { label: '持仓账户', to: '/holdings' },
      { label: '持仓提醒', to: '/holdings/alerts' },
      { label: '模拟账户', to: '/simulation/account' },
      { label: '模拟委托', to: '/simulation/orders' },
      { label: '每日复盘', to: '/review' },
    ],
  },
  {
    group: '情报',
    items: [
      { label: '实时异动', to: '/anomaly' },
      { label: '异动规则', to: '/anomaly/rules' },
      { label: '每日快讯', to: '/info/news-flash' },
      { label: '全网热搜', to: '/info/hot-search' },
      { label: '并购重组', to: '/merger-restructuring' },
    ],
  },
  {
    group: '数据',
    items: [
      { label: '指数总览', to: '/index' },
      { label: '指数成分', to: '/index/components' },
      { label: '行情数据', to: '/data/quotes' },
      { label: '基本面数据', to: '/data/fundamentals' },
      { label: '财务报表', to: '/finance/statements' },
      { label: '财务指标', to: '/finance/indicators' },
      { label: '市场环境历史', to: '/market-env/history' },
    ],
  },
  {
    group: '学院',
    items: [
      { label: '股票基础知识', to: '/academy/basics' },
      { label: '金融书单', to: '/academy/books' },
    ],
  },
  {
    group: '我的',
    items: [
      { label: '个人中心', to: '/profile' },
      { label: '意见反馈', to: '/feedbacks' },
      { label: '升级套餐', to: '/buy' },
    ],
  },
  {
    group: '管理',
    items: [
      { label: '工作台', to: '/workbench' },
      { label: '数据源设置', to: '/settings/data-sources' },
      { label: '系统管理', to: '/system' },
    ],
  },
]

export const FOOTER_NOTE =
  '本平台仅提供数据辅助分析，不提供保本理财及代客操盘服务，投资决策需由您独立作出。'
