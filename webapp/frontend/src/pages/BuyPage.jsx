import { useState } from 'react'
import { Badge, Button, PageShell } from '../components/ui.jsx'
import MembershipModal from '../components/MembershipModal.jsx'
import { api } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { toast } from '../lib/toast.jsx'

function AdminActivate({ onDone }) {
  const [email, setEmail] = useState('')
  const [plan, setPlan] = useState('month')
  const [key, setKey] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    try {
      const user = await api.post('/auth/activate', { email, plan, admin_key: key })
      toast.success('会员已开通', `${user.email} · ${user.membership?.plan_label || plan}`)
      setEmail('')
      setKey('')
      await onDone?.()
    } catch (e2) {
      toast.error('开通失败', e2.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <details className="mt-4 rounded-xl border border-slate-200 bg-white/60 p-4 text-xs dark:border-slate-800 dark:bg-slate-900/60">
      <summary className="cursor-pointer select-none font-medium text-slate-600 dark:text-slate-300">管理员开通入口</summary>
      <form onSubmit={submit} className="mt-3 grid gap-2 sm:grid-cols-4">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="用户邮箱"
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
        />
        <select
          value={plan}
          onChange={(e) => setPlan(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
        >
          <option value="month">月卡 30 天</option>
          <option value="quarter">季卡 90 天</option>
          <option value="year">年卡 365 天</option>
        </select>
        <input
          type="password"
          required
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="管理员密钥"
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
        />
        <Button type="submit" size="sm" loading={loading}>确认开通</Button>
      </form>
    </details>
  )
}

export default function BuyPage() {
  const { membership, refresh } = useAuth()

  return (
    <PageShell
      title="会员订阅"
      subtitle="注册送 3 天试用；到期后按月 / 季 / 年订阅，联系客服 QQ 开通"
      actions={<Badge tone={membership?.status === 'active' ? 'emerald' : membership?.status === 'trial' ? 'amber' : 'rose'}>
        {membership?.status === 'active' ? '会员生效中' : membership?.status === 'trial' ? '免费试用中' : '已到期'}
      </Badge>}
    >
      <div className="mx-auto max-w-[720px]">
        <MembershipModal embedded status={membership?.status || 'expired'} onRefresh={refresh} />
        <div className="mt-4 rounded-xl border border-slate-200 bg-white/60 p-4 text-xs leading-6 text-slate-500 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-400">
          <p>· 首次注册自动获得 <b className="text-slate-700 dark:text-slate-200">3 天全功能试用</b>，无需付款。</p>
          <p>· 试用/会员到期后，策略、异动、AI 分析等页面会进入订阅墙。</p>
          <p>· 支付方式：微信 / 支付宝转账给客服，管理员确认后开通，通常几分钟内生效。</p>
          <p>· 会员有效期：月卡 30 天、季卡 90 天、年卡 365 天；续费从当前到期日顺延。</p>
        </div>
        <AdminActivate onDone={refresh} />
      </div>
    </PageShell>
  )
}
