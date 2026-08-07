/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { authApi } from '../api/client';
import type { AdminUser, LoginResponse } from '../types';

interface AuthContextValue {
  user: AdminUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<AdminUser>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<AdminUser | null>;
  completePasswordChange: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function withLoginFlags(user: AdminUser, response?: LoginResponse): AdminUser {
  if (!response) return user;
  return {
    ...user,
    must_change_password:
      response.must_change_password ??
      response.password_change_required ??
      user.must_change_password ??
      user.password_change_required,
  };
}

export function passwordChangeRequired(user: AdminUser | null): boolean {
  return Boolean(user?.must_change_password ?? user?.password_change_required);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    try {
      const nextUser = await authApi.me();
      setUser(nextUser);
      return nextUser;
    } catch {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    void refreshUser().finally(() => setLoading(false));
  }, [refreshUser]);

  useEffect(() => {
    const handleUnauthorized = () => setUser(null);
    window.addEventListener('myproxy:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('myproxy:unauthorized', handleUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const response = await authApi.login(username, password);
    const loggedInUser = response.user ?? (await authApi.me());
    const nextUser = withLoginFlags(loggedInUser, response);
    setUser(nextUser);
    return nextUser;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const completePasswordChange = useCallback(async () => {
    const refreshed = await refreshUser();
    if (refreshed && passwordChangeRequired(refreshed)) {
      setUser({ ...refreshed, must_change_password: false, password_change_required: false });
    }
  }, [refreshUser]);

  const value = useMemo(
    () => ({ user, loading, login, logout, refreshUser, completePasswordChange }),
    [user, loading, login, logout, refreshUser, completePasswordChange],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
