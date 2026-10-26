/**
 * 个人中心（/profile）：账号资料、统计磁贴、偏好与登录信息。
 *
 * 数据来自 GET /profile（/auth/me 的扩展，可能带 stats 聚合）。
 * 本地系统没有独立的账号服务，退出登录 / 重置密码只保留入口并用 toast 说明，
 * 避免出现「点了没反应」的假按钮。
 */
import { AsyncBlock, Badge, Button, Card, PageShell, SectionHeader, StatTile, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

/** 统计磁贴：后端字段名可能带后缀/复数，按候选键依次取值 */
const STAT_DEFS = [
  { label: '自选数', keys: ['watchlist_count', 'watchlists', 'watchlist', 'watchlist_items'], sub: '自选分组内标的合计' },
  { label: '持仓数', keys: ['holding_count', 'holdings', 'position_count', 'positions'], sub: '在册持仓条目' },
  { label: '反馈数', keys: ['feedback_count', 'feedbacks'], sub: '累计提交的意见反馈' },
  { label: '委托数', keys: ['order_count', 'orders', 'simulation_orders'], sub: '模拟盘委托笔数' },
]

const SCOPE_TEXT = { 1: '全平台', 2: '本部门', 3: '仅本人' }
const PLAN_TEXT = { free: '免费版', pro: '专业版', premium: '专业版', enterprise: '机构版', lifetime: '终身会员' }

function pick(obj, keys) {
  if (!obj) return null
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k]
  }
  return null
}

function Field({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
      <p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-0.5 truncate text-sm font-medium text-slate-800 dark:text-slate-100">{value}</p>
    </div>
  )
}

/** 数据结构兜底：接口可能返回 {user,...}，也可能直接返回扁平用户对象 */
function normalize(raw) {
  const user = (raw && (raw.user || raw.profile)) || raw || {}
  return {
    user,
    tenant: (raw && raw.tenant) || null,
    roles: (raw && raw.roles) || [],
    permissions: (raw && raw.permissions) || [],
    stats: (raw && raw.stats) || user.stats || null,
  }
}

export default function ProfilePage() {
  const { data, loading, error, reload } = useApi(() => api.get('/profile'), [])
  const { user, tenant, roles, permissions, stats } = normalize(data)

  const nickname = user.nickname || user.username || '未设置昵称'
  const initial = (nickname || '?').trim().charAt(0).toUpperCase()
  const statusOk = user.status === undefined || user.status === 1 || user.status === 'active'

  const notConnected = (what) =>
    toast.info('本地演示环境', `${what}依赖账号服务，本页仅保留入口，未做实际变更`)

  return (
    <PageShell
      title="个人中心"
      subtitle="账号资料、会员状态与偏好设置"
      actions={
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => notConnected('退出登录')}>
            退出登录
          </Button>
          <Button variant="ghost" size="sm" onClick={() => notConnected('密码重置')}>
            重置密码
          </Button>
        </div>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        empty={!data}
        emptyText="暂无账号资料"
        onRetry={reload}
        loadingLabel="账号资料加载中"
      >
        {data ? (
          <div className="space-y-4">
            {/* 头像 + 基本身份 */}
            <Card>
              <div className="flex flex-wrap items-center gap-4">
                <div className="grid size-16 shrink-0 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-violet-500 text-2xl font-bold text-white shadow-md">
                  {initial}
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">{nickname}</h2>
                    <Badge tone={statusOk ? 'emerald' : 'rose'}>{statusOk ? '正常' : '已停用'}</Badge>
                    {user.is_vip || user.is_superuser ? (
                      <Badge tone="amber">{user.is_superuser ? '管理员' : 'VIP 会员'}</Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    登录名 {user.username || '—'}
                    {user.email && user.email !== user.username ? ` · ${user.email}` : ''}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <TextLink to="/buy">升级套餐 / 补充额度 →</TextLink>
                    <TextLink to="/feedbacks">提交意见反馈 →</TextLink>
                  </div>
                </div>
              </div>
            </Card>

            {/* 统计磁贴 */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {STAT_DEFS.map((def) => {
                const v = pick(stats, def.keys)
                return (
                  <StatTile
                    key={def.label}
                    label={def.label}
                    value={v === null || v === undefined ? '—' : fmtNum(v, 0)}
                    sub={v === null || v === undefined ? '接口未返回该统计' : def.sub}
                  />
                )
              })}
            </div>

            {/* 会员 */}
            <Card>
              <SectionHeader
                title="我的会员"
                subtitle="MEMBERSHIP · 本地系统按会员状态展示，开通请联系管理员"
                badge={<Badge tone="amber">{PLAN_TEXT[user.plan] || user.plan || '免费版'}</Badge>}
              />
              <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                <span>会员到期：{fmtDateTime(user.plan_expiry) === '—' ? '—' : fmtDateTime(user.plan_expiry)}</span>
                <span>额度：{fmtNum(user.quota_used, 0)} / {fmtNum(user.quota_total, 0)}</span>
                <TextLink to="/buy">升级会员 / 补充额度 →</TextLink>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {[
                  { name: '月卡', price: 68 },
                  { name: '季卡', price: 168 },
                  { name: '年卡', price: 588 },
                ].map((p) => (
                  <div key={p.name} className="rounded-xl border border-slate-200 px-4 py-3 text-center dark:border-slate-700">
                    <p className="text-xs text-slate-500 dark:text-slate-400">{p.name}</p>
                    <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">¥{p.price}</p>
                    <p className="mt-1 text-[10px] text-slate-400">联系管理员 QQ：949394127 获取开通</p>
                  </div>
                ))}
              </div>
            </Card>

            <div className="grid gap-4 xl:grid-cols-2">
              {/* 账号资料 */}
              <Card>
                <SectionHeader title="账号资料" subtitle="用户 ID、昵称与联系方式" />
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <Field label="用户 ID" value={user.id ? `${String(user.id).slice(0, 8)}…` : '—'} />
                  <Field label="昵称" value={user.nickname || '—'} />
                  <Field label="邮箱" value={user.email || '—'} />
                  <Field label="手机号" value={user.phone || '—'} />
                  <Field label="最近登录" value={fmtDateTime(user.last_login_at)} />
                  <Field label="会员到期" value={fmtDateTime(user.member_expire_at)} />
                </div>
              </Card>

              {/* 偏好设置 */}
              <Card>
                <SectionHeader
                  title="偏好设置"
                  subtitle="当前账号的提醒、角色与权限范围"
                  badge={
                    <Badge tone="blue">
                      {PLAN_TEXT[tenant?.plan] || tenant?.plan || '免费版'}
                    </Badge>
                  }
                />
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
                    <span className="text-xs text-slate-500 dark:text-slate-400">消息提醒推送</span>
                    <Badge tone={user.news_alert_enabled ? 'emerald' : 'slate'}>
                      {user.news_alert_enabled ? '已开启' : '已关闭'}
                    </Badge>
                  </div>
                  <Field label="所属租户" value={tenant ? `${tenant.name || '—'}（${tenant.code || '—'}）` : '—'} />
                  <Field label="角色" value={roles.length ? roles.map((r) => r.name || r.code).join('、') : '—'} />
                  <Field label="数据权限范围" value={SCOPE_TEXT[roles[0]?.data_scope] || '—'} />
                </div>
                <p className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
                  已授权权限点 {fmtNum(permissions.length, 0)} 个，菜单可见性由 /permissions/menus 决定。
                </p>
              </Card>
            </div>

            {/* 登录信息 */}
            <Card>
              <SectionHeader title="登录信息" subtitle="最近一次会话与账号状态" />
              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="登录账号" value={user.username || '—'} />
                <Field label="最近登录" value={fmtDateTime(user.last_login_at)} />
                <Field label="账号状态" value={statusOk ? '正常' : '已停用'} />
                <Field
                  label="Token 用量"
                  value={
                    user.token_quota || user.token_used
                      ? `${fmtNum(user.token_used || 0, 0)} / ${fmtNum(user.token_quota || 0, 0)}`
                      : '—'
                  }
                />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 text-[11px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-400">
                <span>退出登录与密码重置在本地系统中仅作展示：</span>
                <button
                  type="button"
                  onClick={() => notConnected('退出登录')}
                  className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
                >
                  退出登录
                </button>
                <span className="text-slate-300 dark:text-slate-600">|</span>
                <button
                  type="button"
                  onClick={() => notConnected('密码重置')}
                  className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
                >
                  重置密码
                </button>
              </div>
            </Card>
          </div>
        ) : null}
      </AsyncBlock>
    </PageShell>
  )
}
