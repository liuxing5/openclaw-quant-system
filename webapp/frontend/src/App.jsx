import React, { Suspense, lazy } from 'react'
import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import MembershipModal from './components/MembershipModal.jsx'
import { Spinner } from './components/ui.jsx'
import { useAuth } from './lib/auth.jsx'

const AuthPage = lazy(() => import('./pages/AuthPage.jsx'))

const RootPage = lazy(() => import('./pages/RootPage.jsx'))
const DashboardPage = lazy(() => import('./pages/DashboardPage.jsx'))
const WatchlistPage = lazy(() => import('./pages/WatchlistPage.jsx'))
const StrategyPage = lazy(() => import('./pages/StrategyPage.jsx'))
const RecommendPage = lazy(() => import('./pages/RecommendPage.jsx'))
const StockAnalysisPage = lazy(() => import('./pages/StockAnalysisPage.jsx'))
const OilGoldPage = lazy(() => import('./pages/OilGoldPage.jsx'))
const LimitLadderPage = lazy(() => import('./pages/LimitLadderPage.jsx'))
const DragonTigerPage = lazy(() => import('./pages/DragonTigerPage.jsx'))
const ConceptAnalysisPage = lazy(() => import('./pages/ConceptAnalysisPage.jsx'))
const IndustryAnalysisPage = lazy(() => import('./pages/IndustryAnalysisPage.jsx'))
const AnomalyPage = lazy(() => import('./pages/AnomalyPage.jsx'))
const AnomalyRulesPage = lazy(() => import('./pages/AnomalyRulesPage.jsx'))
const ReviewPage = lazy(() => import('./pages/ReviewPage.jsx'))
const AcademyPage = lazy(() => import('./pages/AcademyPage.jsx'))
const AcademyBasicsPage = lazy(() => import('./pages/AcademyBasicsPage.jsx'))
const AcademyBooksPage = lazy(() => import('./pages/AcademyBooksPage.jsx'))
const NewsFlashPage = lazy(() => import('./pages/NewsFlashPage.jsx'))
const HotSearchPage = lazy(() => import('./pages/HotSearchPage.jsx'))
const FundFlowPage = lazy(() => import('./pages/FundFlowPage.jsx'))
const GlobalIndexPage = lazy(() => import('./pages/GlobalIndexPage.jsx'))
const MergerPage = lazy(() => import('./pages/MergerPage.jsx'))
const ProfilePage = lazy(() => import('./pages/ProfilePage.jsx'))
const HoldingsPage = lazy(() => import('./pages/HoldingsPage.jsx'))
const HoldingsAlertsPage = lazy(() => import('./pages/HoldingsAlertsPage.jsx'))
const SimulationAccountPage = lazy(() => import('./pages/SimulationAccountPage.jsx'))
const SimulationOrdersPage = lazy(() => import('./pages/SimulationOrdersPage.jsx'))
const IndexOverviewPage = lazy(() => import('./pages/IndexOverviewPage.jsx'))
const IndexComponentsPage = lazy(() => import('./pages/IndexComponentsPage.jsx'))
const DataQuotesPage = lazy(() => import('./pages/DataQuotesPage.jsx'))
const DataFundamentalsPage = lazy(() => import('./pages/DataFundamentalsPage.jsx'))
const FinanceStatementsPage = lazy(() => import('./pages/FinanceStatementsPage.jsx'))
const FinanceIndicatorsPage = lazy(() => import('./pages/FinanceIndicatorsPage.jsx'))
const MarketEnvPage = lazy(() => import('./pages/MarketEnvPage.jsx'))
const MarketEnvHistoryPage = lazy(() => import('./pages/MarketEnvHistoryPage.jsx'))
const BuyPage = lazy(() => import('./pages/BuyPage.jsx'))
const FeedbacksPage = lazy(() => import('./pages/FeedbacksPage.jsx'))
const MyFeedbacksPage = lazy(() => import('./pages/MyFeedbacksPage.jsx'))
const ForbiddenPage = lazy(() => import('./pages/ForbiddenPage.jsx'))
const WorkbenchPage = lazy(() => import('./pages/WorkbenchPage.jsx'))
const WorkbenchMonitorPage = lazy(() => import('./pages/WorkbenchMonitorPage.jsx'))
const WorkbenchStudentsPage = lazy(() => import('./pages/WorkbenchStudentsPage.jsx'))
const WorkbenchPaymentsPage = lazy(() => import('./pages/WorkbenchPaymentsPage.jsx'))
const WorkbenchFeedbacksPage = lazy(() => import('./pages/WorkbenchFeedbacksPage.jsx'))
const WorkbenchSettingsGeneralPage = lazy(() => import('./pages/WorkbenchSettingsGeneralPage.jsx'))
const WorkbenchSettingsAiPage = lazy(() => import('./pages/WorkbenchSettingsAiPage.jsx'))
const SettingsDataSourcesPage = lazy(() => import('./pages/SettingsDataSourcesPage.jsx'))
const SettingsGeneralPage = lazy(() => import('./pages/SettingsGeneralPage.jsx'))
const SettingsAiPage = lazy(() => import('./pages/SettingsAiPage.jsx'))
const SystemPage = lazy(() => import('./pages/SystemPage.jsx'))
const SystemUsersPage = lazy(() => import('./pages/SystemUsersPage.jsx'))
const SystemPermissionsPage = lazy(() => import('./pages/SystemPermissionsPage.jsx'))
const SystemAuditLogsPage = lazy(() => import('./pages/SystemAuditLogsPage.jsx'))

export default function App() {
  const { user, loading, membership, refresh } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-[#080d17]">
        <Spinner label="正在恢复登录状态…" />
      </div>
    )
  }

  if (location.pathname === '/login' || location.pathname === '/register') {
    return (
      <Suspense fallback={<div className="grid min-h-screen place-items-center bg-[#080d17]"><Spinner label="加载中…" /></div>}>
        <AuthPage mode={location.pathname === '/register' ? 'register' : 'login'} />
      </Suspense>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  const paywall = membership?.status === 'expired' && !['/buy', '/profile'].includes(location.pathname)
  if (paywall) {
    return (
      <div className="min-h-screen bg-[#080d17]">
        <MembershipModal
          status="expired"
          onRefresh={refresh}
          onClose={() => navigate('/profile')}
        />
      </div>
    )
  }

  return (
    <Layout>
      <Suspense
        fallback={
          <div className="flex min-h-60 items-center justify-center">
            <Spinner label="页面加载中…" />
          </div>
        }
      >
        <Routes>
          <Route path="/" element={<RootPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/recommend" element={<RecommendPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/strategy" element={<StrategyPage />} />
          <Route path="/strategy/:id" element={<StrategyPage />} />
          <Route path="/stock-analysis" element={<StockAnalysisPage />} />
          <Route path="/stock-analysis/:symbol" element={<StockAnalysisPage />} />
          <Route path="/oil-gold" element={<OilGoldPage />} />
          <Route path="/limit-ladder" element={<LimitLadderPage />} />
          <Route path="/dragon-tiger" element={<DragonTigerPage />} />
          <Route path="/concept-analysis" element={<ConceptAnalysisPage />} />
          <Route path="/concept-analysis/:code" element={<ConceptAnalysisPage />} />
          <Route path="/industry-analysis" element={<IndustryAnalysisPage />} />
          <Route path="/industry-analysis/:code" element={<IndustryAnalysisPage />} />
          <Route path="/anomaly" element={<AnomalyPage />} />
          <Route path="/anomaly/rules" element={<AnomalyRulesPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/academy" element={<AcademyPage />} />
          <Route path="/academy/basics" element={<AcademyBasicsPage />} />
          <Route path="/academy/books" element={<AcademyBooksPage />} />
          <Route path="/info/news-flash" element={<NewsFlashPage />} />
          <Route path="/info/hot-search" element={<HotSearchPage />} />
          <Route path="/info/fund-flow" element={<FundFlowPage />} />
          <Route path="/global-index" element={<GlobalIndexPage />} />
          <Route path="/merger-restructuring" element={<MergerPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/holdings" element={<HoldingsPage />} />
          <Route path="/holdings/alerts" element={<HoldingsAlertsPage />} />
          <Route path="/simulation/account" element={<SimulationAccountPage />} />
          <Route path="/simulation/orders" element={<SimulationOrdersPage />} />
          <Route path="/index" element={<IndexOverviewPage />} />
          <Route path="/index/components" element={<IndexComponentsPage />} />
          <Route path="/data/quotes" element={<DataQuotesPage />} />
          <Route path="/data/fundamentals" element={<DataFundamentalsPage />} />
          <Route path="/finance/statements" element={<FinanceStatementsPage />} />
          <Route path="/finance/indicators" element={<FinanceIndicatorsPage />} />
          <Route path="/market-env" element={<MarketEnvPage />} />
          <Route path="/market-env/history" element={<MarketEnvHistoryPage />} />
          <Route path="/buy" element={<BuyPage />} />
          <Route path="/feedbacks" element={<FeedbacksPage />} />
          <Route path="/feedbacks/my" element={<MyFeedbacksPage />} />
          <Route path="/403" element={<ForbiddenPage />} />
          <Route path="/workbench" element={<WorkbenchPage />} />
          <Route path="/workbench/monitor" element={<WorkbenchMonitorPage />} />
          <Route path="/workbench/students" element={<WorkbenchStudentsPage />} />
          <Route path="/workbench/payments" element={<WorkbenchPaymentsPage />} />
          <Route path="/workbench/feedbacks" element={<WorkbenchFeedbacksPage />} />
          <Route path="/workbench/settings/general" element={<WorkbenchSettingsGeneralPage />} />
          <Route path="/workbench/settings/ai" element={<WorkbenchSettingsAiPage />} />
          <Route path="/settings/data-sources" element={<SettingsDataSourcesPage />} />
          <Route path="/settings/general" element={<SettingsGeneralPage />} />
          <Route path="/settings/ai" element={<SettingsAiPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/system/users" element={<SystemUsersPage />} />
          <Route path="/system/permissions" element={<SystemPermissionsPage />} />
          <Route path="/system/audit-logs" element={<SystemAuditLogsPage />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}
