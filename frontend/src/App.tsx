import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AppLayout } from './components/layout/AppLayout';
import { onUnauthorized, session } from './api/client';
import { resetBrand } from './hooks/useBrand';

// Pages matching PDF
import { LoginPage } from './pages/LoginPage';
import { SetupPage } from './pages/SetupPage';
import { DashboardPage } from './pages/DashboardPage';
import { BrandProfilePage } from './pages/BrandProfilePage';
import { SocialMonitoringPage } from './pages/SocialMonitoringPage';
import { AppStoreMonitoringPage } from './pages/AppStoreMonitoringPage';
import { LookalikeLabPage } from './pages/LookalikeLabPage';
import { SourceHealthPage } from './pages/SourceHealthPage';
import { ReportsPage } from './pages/ReportsPage';
import { SettingsPage } from './pages/SettingsPage';
import { WebThreatsPage } from './pages/WebThreatsPage';
import { InvestigationsPage } from './pages/InvestigationsPage';
import { EvaluationPage } from './pages/EvaluationPage';

function RequireAuth() {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(
    () =>
      onUnauthorized(() => {
        resetBrand();
        navigate('/login', { replace: true });
      }),
    [navigate]
  );

  // No silent offline session: without a token the user must sign in.
  if (!session.isAuthenticated()) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  return <Outlet />;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 1. /login: Sign in */}
        <Route path="/login" element={<LoginPage />} />

        <Route element={<RequireAuth />}>
          {/* 2. /setup: 3-step first-run (the admin must be signed in) */}
          <Route path="/setup" element={<SetupPage />} />

          {/* 3. /dashboard */}
          <Route
            path="/dashboard"
            element={
              <AppLayout>
                <DashboardPage />
              </AppLayout>
            }
          />

          {/* 4. /brand: Brand Profile */}
          <Route
            path="/brand"
            element={
              <AppLayout>
                <BrandProfilePage />
              </AppLayout>
            }
          />

          {/* 5. /social: Social Media Monitoring */}
          <Route
            path="/social"
            element={
              <AppLayout>
                <SocialMonitoringPage />
              </AppLayout>
            }
          />

          {/* 6. /apps: App Store Monitoring */}
          <Route
            path="/apps"
            element={
              <AppLayout>
                <AppStoreMonitoringPage />
              </AppLayout>
            }
          />

          <Route
            path="/web"
            element={
              <AppLayout>
                <WebThreatsPage />
              </AppLayout>
            }
          />
          <Route
            path="/investigations"
            element={
              <AppLayout>
                <InvestigationsPage />
              </AppLayout>
            }
          />

          {/* 7. /lookalike: Look-alike Lab */}
          <Route
            path="/lookalike"
            element={
              <AppLayout>
                <LookalikeLabPage />
              </AppLayout>
            }
          />

          {/* 8. /sources: Source Health */}
          <Route
            path="/sources"
            element={
              <AppLayout>
                <SourceHealthPage />
              </AppLayout>
            }
          />

          {/* 9. /reports */}
          <Route
            path="/reports"
            element={
              <AppLayout>
                <ReportsPage />
              </AppLayout>
            }
          />

          {/* 10. /settings */}
          <Route
            path="/settings"
            element={
              <AppLayout>
                <SettingsPage />
              </AppLayout>
            }
          />

          {/* 11. /evaluation: precision, recall and F1 (linked from Settings) */}
          <Route
            path="/evaluation"
            element={
              <AppLayout>
                <EvaluationPage />
              </AppLayout>
            }
          />
        </Route>

        {/* Redirects */}
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
