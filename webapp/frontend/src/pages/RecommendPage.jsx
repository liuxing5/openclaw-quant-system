import { Card, PageShell } from '../components/ui.jsx'

const FUNNEL_URL = 'https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html'

/** 荐股：左侧导航进入，右侧直接嵌入五策略漏斗页面。 */
export default function RecommendPage() {
  return (
    <PageShell
      title="荐股"
      subtitle="五策略对比选股系统 · 每日 15:10 更新"
      actions={
        <a href={FUNNEL_URL} target="_blank" rel="noreferrer" className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400">
          新窗口打开 ↗
        </a>
      }
    >
      <Card className="overflow-hidden p-0">
        <iframe
          title="五策略对比选股系统"
          src={FUNNEL_URL}
          className="h-[calc(100vh-170px)] min-h-[780px] w-full border-0 bg-[#0b1220]"
          loading="eager"
          referrerPolicy="no-referrer-when-downgrade"
        />
      </Card>
    </PageShell>
  )
}
