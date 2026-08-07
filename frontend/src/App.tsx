import { lazy, Suspense, useEffect } from 'react';
import { passwordChangeRequired, useAuth } from './auth/AuthContext';
import { FullPageLoader } from './components/FullPageLoader';
import { AppShell } from './layouts/AppShell';
import { useRouter } from './router/RouterContext';

const ChangePasswordPage = lazy(() => import('./pages/ChangePasswordPage').then((module) => ({ default: module.ChangePasswordPage })));
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })));
const LoginPage = lazy(() => import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })));
const NodesPage = lazy(() => import('./pages/NodesPage').then((module) => ({ default: module.NodesPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })));
const SubscriptionPage = lazy(() => import('./pages/SubscriptionPage').then((module) => ({ default: module.SubscriptionPage })));

function Redirect({ to, state }: { to: string; state?: unknown }) {
  const { navigate } = useRouter();
  useEffect(() => navigate(to, { replace: true, state }), [navigate, state, to]);
  return <FullPageLoader />;
}

function RoutedApp() {
  const { user, loading } = useAuth();
  const { location } = useRouter();
  const pathname = location.pathname !== '/' ? location.pathname.replace(/\/+$/, '') : '/';

  if (loading) return <FullPageLoader />;
  if (!user) {
    return pathname === '/login'
      ? <LoginPage />
      : <Redirect to="/login" state={{ from: pathname }} />;
  }
  if (passwordChangeRequired(user)) {
    return pathname === '/change-password'
      ? <ChangePasswordPage />
      : <Redirect to="/change-password" />;
  }
  if (pathname === '/login') return <Redirect to="/" />;
  if (pathname === '/change-password') return <ChangePasswordPage />;

  const page = pathname === '/'
    ? <DashboardPage />
    : pathname === '/nodes'
      ? <NodesPage />
      : pathname === '/subscription'
        ? <SubscriptionPage />
        : pathname === '/settings'
          ? <SettingsPage />
          : null;
  if (!page) return <Redirect to="/" />;
  return <AppShell>{page}</AppShell>;
}

export default function App() {
  return (
    <Suspense fallback={<FullPageLoader />}>
      <RoutedApp />
    </Suspense>
  );
}
