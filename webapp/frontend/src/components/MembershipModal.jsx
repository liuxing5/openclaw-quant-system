import { useState } from 'react'
import { Badge, Button } from './ui.jsx'
import { toast } from '../lib/toast.jsx'

const PLANS = [
  { id: 'month', name: '月卡', price: 68 },
  { id: 'quarter', name: '季卡', price: 168, hot: true },
  { id: 'year', name: '年卡', price: 588 },
]

/** 订阅墙：仿原站会员弹窗；embedded=true 时用于 /buy 页内嵌。 */
export default function MembershipModal({ embedded = false, onClose, onRefresh, status = 'expired' }) {
  const [plan, setPlan] = useState('quarter')
  const [refreshing, setRefreshing] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText('949394127')
      toast.success('客服 QQ 已复制', '打开 QQ 添加 949394127 开通')
    } catch {
      toast.info('客服 QQ：949394127', '请手动复制')
    }
  }

  const refresh = async () => {
    if (!onRefresh) return
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }

  const card = (
    <div className="w-full max-w-[680px] overflow-hidden rounded-2xl border border-amber-400/30 bg-[#0b1220] text-slate-100 shadow-2xl shadow-black/50">
      <div className="relative border-b border-white/10 bg-gradient-to-br from-slate-900 via-slate-900 to-amber-950/40 px-6 pb-5 pt-4 text-center">
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="absolute right-4 top-4 grid size-7 place-items-center rounded-full text-slate-400 transition hover:bg-white/10 hover:text-white"
          >
            ×
          </button>
        ) : null}
        <p className="text-xs font-semibold tracking-[0.22em] text-amber-300">⚡ 量化投研 · 专业会员</p>
        <h2 className="mt-2 text-2xl font-bold text-white">解锁专业量化与 AI 分析</h2>
        <p className="mt-1.5 text-xs text-slate-400">
          {status === 'trial' ? '免费试用中，到期后订阅即可继续使用全部功能' : '会员已到期，续费后立即恢复全部权限'}
        </p>
      </div>

      <div className="px-5 pb-5 pt-4">
        <div className="grid grid-cols-3 gap-3">
          {PLANS.map((p) => {
            const active = plan === p.id
            return (
              <button
                type="button"
                key={p.id}
                onClick={() => setPlan(p.id)}
                className={`relative rounded-xl border px-3 py-4 text-center transition ${
                  active
                    ? 'border-amber-400/80 bg-amber-400/10 shadow-[0_0_0_1px_rgba(251,191,36,0.25)]'
                    : 'border-white/10 bg-white/[0.03] hover:border-white/25'
                }`}
              >
                {p.hot ? (
                  <span className="absolute -top-2 left-1/2 -translate-x-1/2 rounded-full bg-amber-400 px-2 py-0.5 text-[10px] font-semibold text-slate-900">
                    推荐
                  </span>
                ) : null}
                <p className="text-xs text-slate-400">{p.name}</p>
                <p className={`mt-1 text-2xl font-bold ${active ? 'text-amber-300' : 'text-rose-400'}`}>¥{p.price}</p>
              </button>
            )
          })}
        </div>

        <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-950/30 px-4 py-3 text-center">
          <p className="text-sm font-medium text-white">
            <span className="mr-1">💬</span>请添加客服 QQ：<b className="text-emerald-300">949394127</b> 进行开通
          </p>
          <p className="mt-1 text-[11px] text-slate-400">支持微信 / 支付宝转账，管理员确认后为您开通</p>
        </div>

        <Button className="mt-4 w-full !bg-emerald-500 !text-slate-950 hover:!bg-emerald-400" onClick={copy}>
          <span className="mr-1">⧉</span> 复制客服 QQ
        </Button>

        <div className="mt-3 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing || !onRefresh}
            className="text-[11px] text-slate-400 underline-offset-2 hover:text-slate-200 hover:underline disabled:opacity-40"
          >
            {refreshing ? '正在检查…' : '已付款？刷新会员状态'}
          </button>
          <Badge tone="slate">QQ 949394127</Badge>
        </div>
      </div>
    </div>
  )

  if (embedded) return card
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center overflow-y-auto bg-slate-950/80 p-4 backdrop-blur-sm">
      {card}
    </div>
  )
}
