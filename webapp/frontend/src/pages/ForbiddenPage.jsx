import { PageShell, Card } from '../components/ui.jsx'

export default function ForbiddenPage() {
  return (
    <PageShell title="403" subtitle="没有访问权限">
      <Card>
        <p className="text-sm text-slate-500 dark:text-slate-400">页面建设中…</p>
      </Card>
    </PageShell>
  )
}
