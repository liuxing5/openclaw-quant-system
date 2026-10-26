/**
 * 无权限（/403）：权限不足时的模板页。
 *
 * 本系统当前所有页面对登录用户全量放行，此页仅作为「无权限态」的视觉与文案模板，
 * 供后续接入权限码（menu:*）时直接复用。
 */
import { useNavigate } from 'react-router-dom'
import { Button, Card } from '../components/ui.jsx'

export default function ForbiddenPage() {
  const navigate = useNavigate()

  return (
    <div className="fade-in-up flex min-h-[60vh] items-center justify-center">
      <Card className="w-full max-w-xl text-center">
        <p
          className="bg-gradient-to-br from-rose-500 to-amber-400 bg-clip-text text-7xl font-bold tracking-tight text-transparent sm:text-8xl"
          aria-hidden="true"
        >
          403
        </p>
        <h1 className="mt-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
          没有访问该页面的权限
        </h1>
        <p className="mx-auto mt-3 max-w-md text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          当前账号的角色未包含该页面对应的菜单权限码。如需访问，请联系管理员在「系统管理 → 权限点」中核对授权。
        </p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          <Button variant="secondary" onClick={() => navigate(-1)}>
            返回上一页
          </Button>
          <Button onClick={() => navigate('/dashboard')}>回到总台</Button>
          <Button variant="ghost" onClick={() => navigate('/')}>
            回到首页
          </Button>
        </div>
        <p className="mt-4 text-[11px] text-slate-400 dark:text-slate-500">
          权限校验由 /permissions/menus 返回的菜单权限码（menu:*）驱动。
        </p>
      </Card>
    </div>
  )
}
