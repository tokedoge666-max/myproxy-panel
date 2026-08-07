import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

export interface RouterLocation {
  pathname: string;
  state: unknown;
}

interface NavigateOptions {
  replace?: boolean;
  state?: unknown;
}

interface RouterValue {
  location: RouterLocation;
  navigate: (target: string | number, options?: NavigateOptions) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function readLocation(): RouterLocation {
  return { pathname: window.location.pathname || '/', state: window.history.state };
}

function safeInternalTarget(target: string): string {
  if (!target.startsWith('/') || target.startsWith('//') || target.includes('\\')) return '/';
  const parsed = new URL(target, window.location.origin);
  if (parsed.origin !== window.location.origin) return '/';
  return parsed.pathname + parsed.search + parsed.hash;
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<RouterLocation>(readLocation);

  useEffect(() => {
    const handlePopState = () => setLocation(readLocation());
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = useCallback((target: string | number, options: NavigateOptions = {}) => {
    if (typeof target === 'number') {
      window.history.go(target);
      return;
    }
    const safeTarget = safeInternalTarget(target);
    const method = options.replace ? 'replaceState' : 'pushState';
    window.history[method](options.state ?? null, '', safeTarget);
    setLocation(readLocation());
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

// The hook shares the private context with its provider by design.
// eslint-disable-next-line react-refresh/only-export-components
export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter must be used inside RouterProvider');
  return value;
}
