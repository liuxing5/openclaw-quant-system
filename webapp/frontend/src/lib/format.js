/** 数值格式化工具，约定与原站一致：红涨绿跌、亿/万亿分档。 */

export function fmtPct(v, digits = 2, withSign = true) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const sign = withSign && n > 0 ? '+' : ''
  return `${sign}${n.toFixed(digits)}%`
}

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  return Number(v).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 成交额：>=1万亿 用「万亿」，否则「亿」 */
export function fmtAmount(amount) {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return '—'
  const n = Number(amount)
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)} 万亿`
  if (n >= 1e8) return `${(n / 1e8).toFixed(2)} 亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(2)} 万`
  return n.toFixed(0)
}

export function fmtCompact(amount) {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return '—'
  const n = Number(amount)
  if (Math.abs(n) >= 1e12) return `${(n / 1e12).toFixed(2)}万亿`
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

/** 涨跌颜色 class（A 股习惯：红涨绿跌） */
export function pctTone(v) {
  if (v === null || v === undefined || Number(v) === 0) return 'text-slate-500 dark:text-slate-400'
  return Number(v) > 0
    ? 'text-rose-600 dark:text-rose-400'
    : 'text-emerald-600 dark:text-emerald-400'
}

export function pctBg(v) {
  if (v === null || v === undefined || Number(v) === 0) return 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
  return Number(v) > 0
    ? 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300'
    : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
}

/** 把 0.0321 这类小数转成 +3.21% 文本（内部已是百分数时原样输出） */
export function ratioPct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(digits)}%`
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())}`
}

export function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const p = (x) => String(x).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

/** ts_code 000001.SZ -> 市场标识（创/科/北/ST 等徽标用） */
export function marketTag(tsCode = '') {
  const [code, ex] = tsCode.split('.')
  if (code.startsWith('688') || code.startsWith('689')) return '科'
  if (code.startsWith('300') || code.startsWith('301')) return '创'
  if (code.startsWith('8') || code.startsWith('4') || code.startsWith('920')) return '北'
  if (code.startsWith('60')) return '沪'
  if (code.startsWith('00') || code.startsWith('200')) return '深'
  return ex || ''
}

export function boardOf(tsCode = '') {
  const code = tsCode.split('.')[0]
  if (code.startsWith('688') || code.startsWith('689')) return '科创板'
  if (code.startsWith('300') || code.startsWith('301')) return '创业板'
  if (code.startsWith('8') || code.startsWith('4') || code.startsWith('920')) return '北交所'
  return 'A主板'
}

export function baseCode(tsCode = '') {
  return tsCode.split('.')[0]
}
