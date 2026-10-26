/**
 * 数据源设置（/settings/data-sources）：数据源矩阵、启用状态与失败统计。
 *
 * 读接口 /system/data-sources 已上线；启停目前没有对应写接口，
 * 因此开关只做前端态并用 toast 说明「未接入写接口」，不做假的「保存成功」提示。
 */
import { useState } from 'react'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, StatTile } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDate, fmtDateTime, fmtNum } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const TIER_TEXT = { 1: '一级 · 主源', 2: '二级 · 备源', 3: '三级 · 兜底' }

const STATUS_MAP = {
  ok: { tone: 'emerald', label: '正常' },
  error: { tone: 'rose', label: '异常' },
  disabled: { tone: 'slate', label: '已停用' },
}

/** 异常行整体标红，一眼能扫到出问题的源 */
function rowTone(feed) {
  const broken = feed.status === 'error' || Number(feed.consecutive_failures) > 0
  if (broken) return 'bg-rose-50/80 dark:bg-rose-500/10'
  if (!feed.enabled) return 'bg-slate-50/70 opacity-70 dark:bg-slate-800/40'
  return ''
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
        checked ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600'
      }`}
    >
      <span
        className={`absolute top-0.5 size-4 rounded-full bg-white shadow transition-all ${
          checked ? 'left-[18px]' : 'left-0.5'
        }`}
      />
    </button>
  )
}

export default function SettingsDataSourcesPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/system/data-sources'), [])
  // 启停无写接口：仅记录本地覆盖态，用于开关回显
  const [overrides, setOverrides] = useState({})

  const feeds = (data && data.feeds) || []
  const datasets = (data && data.datasets) || []
  const providers = (data && data.providers) || []
  const enabledCount = feeds.filter((f) => overrides[f.id] ?? f.enabled).length
  const badCount = feeds.filter((f) => f.status === 'error' || Number(f.consecutive_failures) > 0).length
  const dsOk = datasets.filter((d) => d.status === 'ok').length
  const empty = !feeds.length && !datasets.length

  function toggle(feed) {
    const next = !(overrides[feed.id] ?? feed.enabled)
    setOverrides((prev) => ({ ...prev, [feed.id]: next }))
    toast.info(
      '数据源启停暂未接入写接口',
      `「${feed.name}」已在本地切换为${next ? '启用' : '停用'}，刷新后仍以服务端配置为准`,
    )
  }

  return (
    <PageShell title="数据源设置" subtitle="数据源矩阵、轮询调度与失败监控">
      <AsyncBlock
        loading={loading}
        error={error}
        empty={empty}
        emptyText="暂无数据源记录"
        emptyHint="数据源初始化后会在这里展示层级、权重与失败统计"
        onRetry={reload}
        loadingLabel="数据源状态加载中"
      >
        {data ? (
          <div className="space-y-4">
            {/* 汇总磁贴 */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatTile label="数据源总数" value={fmtNum(feeds.length, 0)} sub="feed_sources 全量" />
              <StatTile
                label="启用中"
                value={fmtNum(enabledCount, 0)}
                sub={`停用 ${fmtNum(feeds.length - enabledCount, 0)} 个`}
                tone="text-emerald-600 dark:text-emerald-400"
              />
              <StatTile
                label="异常"
                value={fmtNum(badCount, 0)}
                sub="连续失败或最近一次报错"
                tone={badCount ? 'text-rose-600 dark:text-rose-400' : undefined}
              />
              <StatTile
                label="本地数据集"
                value={`${fmtNum(dsOk, 0)} / ${fmtNum(datasets.length, 0)}`}
                sub="底座表可用数量"
                tone={dsOk === datasets.length ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}
              />
            </div>

            {/* 数据源矩阵 */}
            <Card>
              <SectionHeader
                title="数据源矩阵"
                subtitle={data.note || '按层级与权重调度'}
                badge={
                  <Badge tone={badCount ? 'rose' : 'emerald'}>
                    {badCount ? `${badCount} 个异常` : '全部正常'}
                  </Badge>
                }
              />
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[900px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-2 font-medium">名称</th>
                      <th className="pb-2 font-medium">分类</th>
                      <th className="pb-2 font-medium">层级</th>
                      <th className="pb-2 text-right font-medium">权重</th>
                      <th className="pb-2 text-right font-medium">轮询间隔</th>
                      <th className="pb-2 text-center font-medium">启用</th>
                      <th className="pb-2 font-medium">最近成功</th>
                      <th className="pb-2 font-medium">最近错误</th>
                      <th className="pb-2 text-right font-medium">连续失败</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feeds.map((f) => {
                      const enabled = overrides[f.id] ?? f.enabled
                      const st = STATUS_MAP[f.status] || { tone: 'slate', label: f.status || '—' }
                      return (
                        <tr key={f.id} className={`border-t border-slate-100 dark:border-slate-800 ${rowTone(f)}`}>
                          <td className="py-2 pr-3">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-slate-700 dark:text-slate-200">{f.name}</span>
                              <Badge tone={st.tone}>{st.label}</Badge>
                            </div>
                            {f.last_error_msg && f.status === 'error' ? (
                              <p className="mt-0.5 max-w-[280px] truncate text-[11px] text-rose-600 dark:text-rose-300">
                                {f.last_error_msg}
                              </p>
                            ) : null}
                          </td>
                          <td className="py-2 pr-3 text-slate-500 dark:text-slate-400">{f.category || '—'}</td>
                          <td className="py-2 pr-3 text-slate-500 dark:text-slate-400">
                            {TIER_TEXT[f.tier] || `第 ${f.tier} 层`}
                          </td>
                          <td className="py-2 pr-3 text-right tabular-nums text-slate-500 dark:text-slate-400">
                            {f.weight ?? '—'}
                          </td>
                          <td className="py-2 pr-3 text-right tabular-nums text-slate-500 dark:text-slate-400">
                            {f.poll_interval_sec ? `${f.poll_interval_sec}s` : '—'}
                          </td>
                          <td className="py-2 pr-3 text-center">
                            <span className="inline-flex justify-center">
                              <Toggle
                                checked={enabled}
                                onChange={() => toggle(f)}
                                label={`${f.name} 启用开关`}
                              />
                            </span>
                          </td>
                          <td className="py-2 pr-3 tabular-nums text-slate-500 dark:text-slate-400">
                            {fmtDateTime(f.last_success_at)}
                          </td>
                          <td className="py-2 pr-3 tabular-nums text-slate-500 dark:text-slate-400">
                            {f.last_error_at ? fmtDateTime(f.last_error_at) : '—'}
                          </td>
                          <td
                            className={`py-2 text-right font-semibold tabular-nums ${
                              Number(f.consecutive_failures) > 0
                                ? 'text-rose-600 dark:text-rose-400'
                                : 'text-slate-400 dark:text-slate-500'
                            }`}
                          >
                            {fmtNum(f.consecutive_failures || 0, 0)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
                层级越高优先级越高，同一层级内按权重分配请求；权重列为空表示该源未单独配置权重。
                开关暂未接入写接口，切换仅作本地预览。
              </p>
            </Card>

            {/* 数据源通道 */}
            <Card>
              <SectionHeader
                title="接入通道"
                subtitle={`更新于 ${fmtDateTime(data.updated_at)}`}
                badge={<Badge tone="blue">{providers.length} 个通道</Badge>}
              />
              <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {providers.map((p) => (
                  <div
                    key={p.id}
                    className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold text-slate-700 dark:text-slate-200">
                        {p.name}
                      </span>
                      <Badge tone={p.enabled ? 'emerald' : 'slate'}>{p.enabled ? '启用' : '停用'}</Badge>
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-slate-400 dark:text-slate-500">
                      {p.kind}
                    </p>
                    <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{p.auth}</p>
                  </div>
                ))}
              </div>
            </Card>

            {/* 本地数据集 */}
            <Card>
              <SectionHeader
                title="本地数据集"
                subtitle="PostgreSQL 底座的行数与新鲜度"
                badge={
                  <Badge tone={dsOk === datasets.length ? 'emerald' : 'amber'}>
                    {dsOk}/{datasets.length} 可用
                  </Badge>
                }
              />
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[560px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-2 font-medium">数据集</th>
                      <th className="pb-2 text-right font-medium">行数</th>
                      <th className="pb-2 font-medium">最新数据日</th>
                      <th className="pb-2 text-right font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datasets.map((d) => (
                      <tr
                        key={d.id}
                        className={`border-t border-slate-100 dark:border-slate-800 ${
                          d.status === 'ok' ? '' : 'bg-rose-50/80 dark:bg-rose-500/10'
                        }`}
                      >
                        <td className="py-2 pr-3 font-medium text-slate-700 dark:text-slate-200">{d.name}</td>
                        <td className="py-2 pr-3 text-right tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtNum(d.rows || 0, 0)}
                        </td>
                        <td className="py-2 pr-3 tabular-nums text-slate-500 dark:text-slate-400">
                          {d.latest ? fmtDate(d.latest) : '—'}
                        </td>
                        <td className="py-2 text-right">
                          <Badge tone={d.status === 'ok' ? 'emerald' : 'rose'}>
                            {d.status === 'ok' ? '正常' : '异常'}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {datasets.some((d) => d.detail) ? (
                <p className="mt-2 text-[11px] text-rose-600 dark:text-rose-300">
                  {datasets.filter((d) => d.detail).map((d) => `${d.name}：${d.detail}`).join('；')}
                </p>
              ) : null}
            </Card>
          </div>
        ) : null}
      </AsyncBlock>
    </PageShell>
  )
}
