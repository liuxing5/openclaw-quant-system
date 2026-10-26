import { useEffect } from 'react'
import { Button } from './ui.jsx'
import { AGREEMENTS } from '../lib/agreements.js'

/** 用户服务协议 / 风险揭示书弹窗。内容为平台自写，不是原站照搬。 */
export default function LegalModal({ docKey, onClose, onAgree }) {
  const doc = docKey ? AGREEMENTS[docKey] : null

  useEffect(() => {
    if (!doc) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [doc, onClose])

  if (!doc) return null

  return (
    <div
      className="fixed inset-0 z-[120] grid place-items-center bg-slate-950/80 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.()
      }}
    >
      <div className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0b1220] text-slate-100 shadow-2xl shadow-black/50">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <h2 className="text-base font-semibold">{doc.title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="grid size-7 place-items-center rounded-full text-slate-400 transition hover:bg-white/10 hover:text-white"
          >
            ×
          </button>
        </div>

        <div className="border-b border-white/10 px-5 py-3">
          <p className="rounded-lg border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-[11px] leading-5 text-amber-200">
            {doc.notice}
          </p>
        </div>

        <div className="thin-scroll flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {doc.sections.map((s) => (
            <section key={s.title}>
              <h3 className="text-xs font-semibold text-slate-200">{s.title}</h3>
              <p className="mt-1.5 text-[11px] leading-6 text-slate-400">{s.text}</p>
            </section>
          ))}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-white/10 px-5 py-3">
          <Button variant="secondary" size="sm" onClick={onClose}>
            关闭
          </Button>
          <Button
            size="sm"
            onClick={() => {
              onAgree?.(docKey)
              onClose?.()
            }}
          >
            我已知晓并同意
          </Button>
        </div>
      </div>
    </div>
  )
}
