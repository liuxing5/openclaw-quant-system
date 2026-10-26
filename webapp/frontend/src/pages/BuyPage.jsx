/**
 * 升级套餐（/buy）：免费 / 专业 / 机构 三档对比与开通入口。
 *
 * 原站此页未登录会被重定向到登录页，本地系统不跳转，直接渲染套餐内容；
 * 价格与权益为本地静态配置（口径取自 spec/pages/buy.md、profile.md 的月/季/年卡）。
 */
import { useState } from 'react'
import { Badge, Button, Card, PageShell, SectionHeader } from '../components/ui.jsx'
import { toast } from '../lib/toast.jsx'

const PLANS = [
  {
    id: 'free',
    name: '免费版',
    price: '¥0',
    unit: '永久免费',
    tagline: '覆盖基础投研流程，适合先跑起来看效果',
    features: [
      '基础行情与涨跌分布（收盘后更新）',
      '自选分组 + 持仓管家，单分组 50 只',
      '模拟盘撮合（市价 / 限价单）',
      '每日复盘查看（生成后可读）',
      '单数据源轮询，600 秒刷新',
    ],
    limits: ['量化策略扫描每日 3 次', '不含 AI 自动选股与推送', '数据源优先级最低'],
  },
  {
    id: 'pro',
    name: '专业版',
    price: '¥68',
    unit: '每月',
    tagline: '个人投资者主力档位，策略 + AI + 实时提醒全开',
    priceLine: '月卡 ¥68 · 季卡 ¥168 · 年卡 ¥588',
    recommended: true,
    features: [
      '实时行情（交易时段 10 秒级刷新）',
      '6 量化策略全量扫描 + 历史绩效回测',
      'AI 自动选股，按策略命中排序',
      '每日复盘自动生成 + 情绪打分历史',
      '异动实时监控（急拉 / 放量 / 涨停）',
      '龙虎榜、资金流向、连板梯队全量',
      '多分组自选不限量，行情列实时刷新',
      '优先数据源轮询（60 秒间隔）',
    ],
    limits: ['不含多成员协作与 API 导出'],
  },
  {
    id: 'enterprise',
    name: '机构版',
    price: '¥298',
    unit: '每月',
    tagline: '团队投研与对外输出，含协作、导出与定制',
    features: [
      '包含专业版全部权益',
      '多成员协作，角色与权限矩阵管理',
      '行情 / 基本面 / 财报批量导出',
      '专属数据源通道与自定义轮询间隔',
      '审计日志与操作留痕',
      '策略与页面专属 DIY 定制',
      '专属客服对接（QQ：949394127）',
    ],
    limits: [],
  },
]

/** 权益对比表：true=✓，false=—，字符串=有限度提供 */
const COMPARE = [
  { label: '实时行情刷新', free: '—', pro: '✓', ent: '✓' },
  { label: '日线与历史数据', free: '✓', pro: '✓', ent: '✓' },
  { label: '自选分组', free: '1 组 / 50 只', pro: '不限', ent: '不限' },
  { label: '模拟盘撮合', free: '✓', pro: '✓', ent: '✓' },
  { label: '量化策略扫描', free: '每日 3 次', pro: '不限', ent: '不限' },
  { label: '策略回测与绩效跟踪', free: '—', pro: '✓', ent: '✓' },
  { label: 'AI 自动选股', free: '—', pro: '✓', ent: '✓' },
  { label: '每日复盘自动生成', free: '—', pro: '✓', ent: '✓' },
  { label: '复盘报告邮件推送', free: '—', pro: '✓', ent: '✓' },
  { label: '实时异动监控', free: '—', pro: '✓', ent: '✓' },
  { label: '数据源轮询间隔', free: '600 秒', pro: '60 秒', ent: '可配置' },
  { label: '多成员协作', free: '—', pro: '—', ent: '✓' },
  { label: 'API 数据导出', free: '—', pro: '—', ent: '✓' },
  { label: '专属客服与定制开发', free: '—', pro: '—', ent: '✓' },
]

function Cell({ value }) {
  if (value === '✓') return <span className="font-semibold text-emerald-600 dark:text-emerald-400">✓</span>
  if (value === '—') return <span className="text-slate-300 dark:text-slate-600">—</span>
  return <span className="text-slate-600 dark:text-slate-300">{value}</span>
}

export default function BuyPage() {
  const [selected, setSelected] = useState('pro')

  // 本地演示环境没有支付网关，点击开通只做提示，不产生任何订单
  function openPlan(plan) {
    setSelected(plan.id)
    toast.info(
      '本地演示环境，未接入支付',
      `已选择「${plan.name}」，正式环境请联系管理员 QQ：949394127 开通`,
    )
  }

  return (
    <PageShell title="升级套餐" subtitle="按投研强度选择版本，随时可切换">
      <div className="space-y-4">
        {/* 套餐卡 */}
        <div className="grid gap-3 lg:grid-cols-3">
          {PLANS.map((plan) => {
            const active = selected === plan.id
            return (
              <Card
                key={plan.id}
                onClick={() => setSelected(plan.id)}
                className={`relative flex flex-col transition ${
                  active
                    ? 'border-brand-500 ring-2 ring-brand-500/30 dark:border-brand-400'
                    : 'cursor-pointer hover:border-slate-300 dark:hover:border-slate-600'
                }`}
              >
                {plan.recommended ? (
                  <Badge tone="amber" className="absolute -top-2.5 left-4">
                    🔥 推荐
                  </Badge>
                ) : null}
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{plan.name}</h2>
                    <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{plan.tagline}</p>
                  </div>
                  {active ? <Badge tone="blue">当前选择</Badge> : null}
                </div>

                <div className="mt-3 flex items-end gap-1.5">
                  <span className="text-3xl font-bold tabular-nums text-slate-900 dark:text-slate-50">
                    {plan.price}
                  </span>
                  <span className="pb-1 text-xs text-slate-400 dark:text-slate-500">/ {plan.unit}</span>
                </div>
                {plan.priceLine ? (
                  <p className="mt-1 text-[11px] tabular-nums text-slate-500 dark:text-slate-400">{plan.priceLine}</p>
                ) : null}

                <ul className="mt-3 flex-1 space-y-1.5">
                  {plan.features.map((f) => (
                    <li key={f} className="flex items-start gap-1.5 text-xs text-slate-600 dark:text-slate-300">
                      <span className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400">✓</span>
                      <span className="min-w-0">{f}</span>
                    </li>
                  ))}
                  {plan.limits.map((f) => (
                    <li key={f} className="flex items-start gap-1.5 text-xs text-slate-400 dark:text-slate-500">
                      <span className="mt-0.5 shrink-0">·</span>
                      <span className="min-w-0">{f}</span>
                    </li>
                  ))}
                </ul>

                <Button
                  className="mt-4 w-full"
                  variant={active ? 'primary' : 'secondary'}
                  onClick={() => openPlan(plan)}
                >
                  开通{plan.name}
                </Button>
              </Card>
            )
          })}
        </div>

        {/* 权益对比表 */}
        <Card>
          <SectionHeader
            title="权益对比"
            subtitle="同一口径逐项对照，选档前建议先看这里"
            badge={<Badge tone="blue">{PLANS.find((p) => p.id === selected)?.name}</Badge>}
          />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[520px] text-xs">
              <thead>
                <tr className="text-left text-[11px] text-slate-400">
                  <th className="pb-2 font-medium">权益项</th>
                  {PLANS.map((p) => (
                    <th key={p.id} className="pb-2 text-center font-medium">
                      {p.name}
                      <span className="ml-1 font-normal tabular-nums">{p.price}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COMPARE.map((row) => (
                  <tr
                    key={row.label}
                    className="border-t border-slate-100 dark:border-slate-800"
                  >
                    <td className="py-1.5 text-slate-600 dark:text-slate-300">{row.label}</td>
                    <td className="py-1.5 text-center">
                      <Cell value={row.free} />
                    </td>
                    <td className="py-1.5 text-center">
                      <Cell value={row.pro} />
                    </td>
                    <td className="py-1.5 text-center">
                      <Cell value={row.ent} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* 开通说明 */}
        <Card>
          <SectionHeader title="开通说明" subtitle="本地演示环境的付费口径" />
          <ul className="mt-3 space-y-1.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
            <li>· 首次注册赠送 1 天免费体验，体验期内可浏览全部付费页面。</li>
            <li>· 正式开通请联系管理员 QQ：949394127，支付后由管理员在后台开通对应档位。</li>
            <li>· 会员到期后账号自动回落到免费版，已提交的反馈、自选与持仓数据保留。</li>
            <li>· 本页不产生真实订单：当前为本地演示环境，未接入支付网关。</li>
          </ul>
        </Card>
      </div>
    </PageShell>
  )
}
