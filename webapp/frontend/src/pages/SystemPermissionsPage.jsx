/**
 * /system/permissions 权限管理：角色 × 权限点矩阵（行 = 权限项，列 = 角色）。
 *
 * 读接口 GET /system/permissions 的具体字段由后端 D3 单元实现，这里做了多形状兼容
 * （roles 为数组/映射、permissions 在 permissions/perms/items、矩阵在 matrix/grants
 * 或反查 role.permissions / permission.roles），避免字段名微调就让整页塌掉。
 *
 * 写交互的取舍：本地环境没有权限落库接口，所以开关是**前端态** —— 点击只更新本页状态
 * 并 toast 说明「不落库」，不伪装成已保存的真实授权（见页面顶部说明条）。
 */
import React, { useMemo, useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { toast } from '../lib/toast.jsx'

const arr = (v) => (Array.isArray(v) ? v : [])

function pick(obj, keys, fallback = null) {
  if (!obj) return fallback
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return fallback
}

const GROUP_LABEL = {
  nav: '菜单导航',
  menu: '菜单导航',
  data: '数据访问',
  admin: '系统管理',
  api: '接口调用',
  biz: '业务操作',
  sys: '系统管理',
}

const GROUP_HINT = {
  nav: '控制左侧菜单可见性，关掉即从导航隐藏',
  menu: '控制左侧菜单可见性，关掉即从导航隐藏',
  data: '控制行情 / 财务 / 数据表等读接口',
  admin: '用户、权限、审计、设置等管理动作',
  api: '写接口与高频轮询接口的调用配额',
  biz: '自选、持仓、模拟盘等业务功能',
  sys: '系统级开关',
}

/** 把后端可能返回的多种形状统一成 { roles, perms, grants }。 */
function normalize(raw) {
  if (!raw) return { roles: [], perms: [], grants: {} }

  // ---- 角色 ----
  let rawRoles = raw.roles ?? raw.role_list ?? raw.role_matrix ?? null
  if (rawRoles && !Array.isArray(rawRoles) && typeof rawRoles === 'object') {
    rawRoles = Object.entries(rawRoles).map(([key, v]) =>
      v && typeof v === 'object' ? { key, ...v } : { key, name: v },
    )
  }
  const roles = arr(rawRoles).map((r, i) => {
    if (typeof r === 'string') return { key: r, name: r, desc: '', perms: [] }
    return {
      key: String(pick(r, ['code', 'key', 'id', 'slug', 'role'], `role-${i}`)),
      name: String(pick(r, ['name', 'label', 'title'], `角色 ${i + 1}`)),
      desc: String(pick(r, ['description', 'desc', 'memo', 'remark'], '')),
      perms: arr(r.permissions ?? r.permission_keys ?? r.perms ?? r.allows),
    }
  })

  // ---- 权限点 ----
  const rawPerms = arr(raw.permissions ?? raw.perms ?? raw.items ?? raw.permission_list)
  const perms = rawPerms.map((p, i) => {
    if (typeof p === 'string') return { key: p, name: p, group: null, desc: '', roles: [] }
    return {
      key: String(pick(p, ['key', 'code', 'permission', 'permission_key', 'id'], `perm-${i}`)),
      name: String(pick(p, ['name', 'label', 'title'], pick(p, ['key', 'code'], `权限 ${i + 1}`))),
      group: pick(p, ['group', 'category', 'module', 'namespace', 'prefix'], null),
      desc: String(pick(p, ['description', 'desc', 'memo', 'remark'], '')),
      roles: arr(p.roles ?? p.allowed_roles ?? p.granted_roles ?? p.role_keys),
    }
  })

  // ---- 授权矩阵 ----
  const grants = {}
  const set = (permKey, roleKey, on) => {
    if (!permKey || !roleKey) return
    grants[`${permKey}::${roleKey}`] = !!on
  }
  const matrix = raw.matrix ?? raw.grants ?? raw.permissions_matrix ?? null

  if (Array.isArray(matrix)) {
    matrix.forEach((g) =>
      set(
        pick(g, ['permission', 'permission_key', 'key', 'code']),
        pick(g, ['role', 'role_key', 'role_code']),
        pick(g, ['granted', 'enabled', 'allow', 'value'], true),
      ),
    )
  } else if (matrix && typeof matrix === 'object') {
    const roleKeys = new Set(roles.map((r) => r.key))
    const looksLikeRoleMap = Object.keys(matrix).some((k) => roleKeys.has(k))
    Object.entries(matrix).forEach(([a, v]) => {
      if (looksLikeRoleMap) {
        // { roleKey: [permKey...] } 或 { roleKey: {permKey: bool} }
        if (Array.isArray(v)) v.forEach((pk) => set(pk, a, true))
        else if (v && typeof v === 'object') Object.entries(v).forEach(([pk, on]) => set(pk, a, on))
      } else if (Array.isArray(v)) {
        // { permKey: [roleKey...] }
        v.forEach((rk) => set(a, rk, true))
      } else if (v && typeof v === 'object') {
        // { permKey: {roleKey: bool} }
        Object.entries(v).forEach(([rk, on]) => set(a, rk, on))
      }
    })
  }

  // 兜底：角色自带权限列表 / 权限点自带授权角色
  roles.forEach((r) => r.perms.forEach((pk) => set(pk, r.key, true)))
  perms.forEach((p) => p.roles.forEach((rk) => set(p.key, rk, true)))

  // 角色缺省时从授权结果里反推，保证矩阵一定有列
  const finalRoles = roles.length
    ? roles
    : Array.from(
        Object.keys(grants).reduce((acc, k) => {
          const rk = k.split('::')[1]
          if (rk) acc.add(rk)
          return acc
        }, new Set()),
      ).map((key) => ({ key, name: key, desc: '', perms: [] }))

  return { roles: finalRoles, perms, grants }
}

/* --------------------------------------------------------------- 单元格开关 */

function SwitchCell({ on, onToggle, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onToggle}
      className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        on ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-700'
      }`}
      title={on ? '已开启（点击关闭，仅本地演示）' : '已关闭（点击开启，仅本地演示）'}
    >
      <span
        className={`size-4 rounded-full bg-white shadow-sm transition-transform ${
          on ? 'translate-x-4' : 'translate-x-0.5'
        }`}
      />
    </button>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function SystemPermissionsPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/system/permissions'), [])
  // 前端态覆盖层：key = `${permKey}::${roleKey}`，刷新后随接口数据重置
  const [overrides, setOverrides] = useState({})

  const { roles, perms, grants } = useMemo(() => normalize(data), [data])

  const isOn = (perm, role) => {
    const k = `${perm.key}::${role.key}`
    if (Object.prototype.hasOwnProperty.call(overrides, k)) return overrides[k]
    if (grants[k] !== undefined) return grants[k]
    return false
  }

  const toggle = (perm, role) => {
    const next = !isOn(perm, role)
    setOverrides((prev) => ({ ...prev, [`${perm.key}::${role.key}`]: next }))
    toast.info(
      next ? '已在本页开启' : '已在本页关闭',
      `${role.name} · ${perm.name}：本地环境没有权限写接口，改动不落库，刷新后恢复接口值。`,
    )
  }

  // 按 permission.key 前缀分组（nav.* / data.* / admin.* …）
  const groups = useMemo(() => {
    const buckets = new Map()
    perms.forEach((p) => {
      const raw = p.group || p.key.split('.')[0] || 'other'
      const key = String(raw)
      if (!buckets.has(key)) buckets.set(key, [])
      buckets.get(key).push(p)
    })
    return Array.from(buckets.entries())
      .map(([key, list]) => ({
        key,
        label: GROUP_LABEL[key] || `${key}.*`,
        hint: GROUP_HINT[key] || `key 前缀 ${key}.*`,
        list: [...list].sort((a, b) => a.key.localeCompare(b.key)),
      }))
      .sort((a, b) => a.key.localeCompare(b.key))
  }, [perms])

  const enabledCount = (role) => perms.filter((p) => isOn(p, role)).length

  return (
    <PageShell
      title="权限管理"
      subtitle="角色 × 权限点矩阵：行为权限项，列为角色，按 key 前缀分组"
      actions={
        <div className="flex items-center gap-2">
          {data?.meta ? (
            <Badge tone={data.meta.degraded ? 'rose' : data.meta.seed ? 'amber' : 'slate'}>
              {data.meta.degraded ? '降级数据' : data.meta.seed ? '种子演示数据' : `来源 ${data.meta.source || '—'}`}
            </Badge>
          ) : null}
          <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
            刷新
          </Button>
        </div>
      }
    >
      {/* 交互说明：明确告诉使用者开关是前端态，避免误以为已授权 */}
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
        <b>开关为前端态演示：</b>
        本地环境未接入权限写接口，点击只更新本页状态并给出 toast 提示，刷新后会重新读取接口值
        —— 不会把改动伪装成已保存的授权。需要真实授权请改后端角色配置。
      </div>

      <AsyncBlock loading={loading} error={error} onRetry={reload} loadingLabel="权限矩阵加载中" empty={!loading && !error && perms.length === 0} emptyText="暂无权限点" emptyHint="后端返回 permissions 列表后这里会渲染矩阵">
        <div className="space-y-4">
          {/* 角色说明 */}
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {roles.map((r) => (
              <Card key={r.key} className="p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{r.name}</p>
                    <p className="mt-0.5 font-mono text-[10px] text-slate-400">{r.key}</p>
                  </div>
                  <Badge tone="blue">{enabledCount(r)}/{perms.length} 开</Badge>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
                  {r.desc || '后端未提供该角色的说明文案'}
                </p>
              </Card>
            ))}
          </div>

          {/* 分组矩阵 */}
          {groups.map((g) => (
            <Card key={g.key}>
              <SectionHeader
                title={g.label}
                subtitle={g.hint}
                badge={<Badge tone="slate">{g.list.length} 个权限点</Badge>}
              />
              <div className="mt-3 overflow-x-auto thin-scroll">
                <table className="w-full min-w-[560px] text-xs">
                  <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-1.5 font-medium">权限点</th>
                      <th className="pb-1.5 font-medium">说明</th>
                      {roles.map((r) => (
                        <th key={r.key} className="pb-1.5 text-center font-medium">
                          {r.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {g.list.map((p) => (
                      <tr key={p.key} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-2">
                          <span className="font-medium text-slate-700 dark:text-slate-200">{p.name}</span>
                          <span className="ml-1.5 font-mono text-[10px] text-slate-400">{p.key}</span>
                        </td>
                        <td className="max-w-[20rem] truncate py-2 text-slate-500 dark:text-slate-400">
                          {p.desc || '—'}
                        </td>
                        {roles.map((r) => {
                          const on = isOn(p, r)
                          return (
                            <td key={r.key} className="py-2 text-center">
                              <span className="inline-flex items-center gap-1.5">
                                <SwitchCell
                                  on={on}
                                  label={`${r.name} - ${p.name}`}
                                  onToggle={() => toggle(p, r)}
                                />
                                <span
                                  className={`w-4 text-[10px] tabular-nums ${
                                    on ? 'text-brand-600 dark:text-brand-400' : 'text-slate-400'
                                  }`}
                                >
                                  {on ? '开' : '关'}
                                </span>
                              </span>
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
