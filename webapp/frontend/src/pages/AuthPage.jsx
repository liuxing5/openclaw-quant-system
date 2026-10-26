import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '../components/ui.jsx'
import { useAuth } from '../lib/auth.jsx'

export default function AuthPage({ mode = 'login' }) {
  const isRegister = mode === 'register'
  const { user, login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (user) navigate(location.state?.from || '/dashboard', { replace: true })
  }, [user, navigate, location.state])

  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      if (isRegister) await register(email, password, nickname)
      else await login(email, password)
      navigate(location.state?.from || '/dashboard', { replace: true })
    } catch (e2) {
      setErr(e2.message || '操作失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#080d17] text-slate-100">
      <div className="pointer-events-none absolute -left-40 top-0 size-[420px] rounded-full bg-brand-600/20 blur-3xl" />
      <div className="pointer-events-none absolute -right-40 bottom-0 size-[420px] rounded-full bg-amber-500/10 blur-3xl" />
      <div className="relative mx-auto flex min-h-screen max-w-5xl flex-col items-center justify-center px-4 py-10">
        <div className="mb-5 text-center">
          <p className="text-xs font-semibold tracking-[0.3em] text-amber-300">QUANT RESEARCH</p>
          <h1 className="mt-2 text-3xl font-bold">量化投研 · 数据一体化</h1>
          <p className="mt-2 text-sm text-slate-400">注册即送 3 天免费试用 · 到期后订阅月/季/年卡</p>
        </div>

        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0b1220]/95 p-6 shadow-2xl shadow-black/40">
          <h2 className="text-lg font-semibold">{isRegister ? '注册账号' : '登录账号'}</h2>
          <p className="mt-1 text-xs text-slate-400">
            {isRegister ? '首次注册自动获得 3 天全功能试用' : '登录后继续使用策略、异动与 AI 分析'}
          </p>

          <form onSubmit={submit} className="mt-5 space-y-3">
            {isRegister ? (
              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-400">昵称（可选）</span>
                <input
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  placeholder="怎么称呼你"
                  className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-500 focus:border-amber-400/60"
                />
              </label>
            ) : null}
            <label className="block">
              <span className="mb-1 block text-[11px] text-slate-400">邮箱</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-500 focus:border-amber-400/60"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-slate-400">密码</span>
              <input
                type="password"
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="至少 6 位"
                className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-500 focus:border-amber-400/60"
              />
            </label>

            {err ? <p className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">{err}</p> : null}

            <Button type="submit" loading={loading} className="w-full !bg-amber-400 !text-slate-950 hover:!bg-amber-300">
              {isRegister ? '注册并开始 3 天试用' : '登录'}
            </Button>
          </form>

          <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
            <span>{isRegister ? '已有账号？' : '还没有账号？'}</span>
            <Link to={isRegister ? '/login' : '/register'} className="font-medium text-amber-300 hover:text-amber-200">
              {isRegister ? '去登录' : '注册免费试用'}
            </Link>
          </div>
          <p className="mt-4 border-t border-white/10 pt-3 text-center text-[10px] text-slate-500">
            本平台仅提供数据辅助分析，不构成投资建议 · 客服 QQ 949394127
          </p>
        </div>
      </div>
    </div>
  )
}
