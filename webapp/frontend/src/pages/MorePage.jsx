import React from 'react'
import { Link } from 'react-router-dom'
import { Card, PageShell, SectionHeader } from '../components/ui.jsx'

const SECTIONS = [
  {
    title: '行情与资金',
    items: [
      ['外围指数', '/global-index', '美股 / 港股 / 日经 / 欧股一屏行情'],
      ['板块资金明细', '/info/fund-flow', '概念 / 行业全量资金表'],
      ['指数成分', '/index/components', '指数成分股与权重'],
    ],
  },
  {
    title: '数据工具',
    items: [
      ['基本面数据', '/data/fundamentals', '全市场财报与估值大表'],
      ['财务指标', '/finance/indicators', '单只股票盈利 / 成长 / 偿债'],
      ['环境历史', '/market-env/history', '情绪、涨跌家数、成交额曲线'],
    ],
  },
  {
    title: '交易与管理',
    items: [
      ['客户信息', '/workbench/students', '注册客户、套餐、到期时间与最近登录'],
      ['订单与收款', '/workbench/payments', '微信 / 支付宝人工收款与会员开通'],
      ['持仓提醒', '/holdings/alerts', '价格条件提醒与触发记录'],
      ['模拟委托', '/simulation/orders', '全部委托与撤单'],
      ['股票学院', '/academy', '基础课程与书单'],
      ['管理工作台', '/workbench', '用户、内容与运营'],
      ['系统设置', '/system', '数据源、权限与审计'],
      ['升级套餐', '/buy', '会员权益与订阅'],
    ],
  },
]

export default function MorePage() {
  return (
    <PageShell title="全部功能" subtitle="不常用的工具收在这里，主导航只保留高频入口">
      <div className="grid gap-4 lg:grid-cols-3">
        {SECTIONS.map((section) => (
          <Card key={section.title}>
            <SectionHeader title={section.title} />
            <div className="mt-3 space-y-1.5">
              {section.items.map(([label, to, desc]) => (
                <Link
                  key={to}
                  to={to}
                  className="block rounded-lg border border-slate-200 px-3 py-2.5 transition hover:border-brand-300 hover:bg-brand-50/40 dark:border-slate-700 dark:hover:border-brand-500/50 dark:hover:bg-brand-500/10"
                >
                  <p className="text-xs font-medium text-slate-700 dark:text-slate-200">{label}</p>
                  <p className="mt-0.5 text-[10px] text-slate-400">{desc}</p>
                </Link>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </PageShell>
  )
}
