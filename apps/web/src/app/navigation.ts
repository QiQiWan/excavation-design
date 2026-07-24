export type BrowserRoute = {
  pathname: string;
  search: string;
  hash: string;
};

export const LOGIN_PATH = '/login';

export function projectPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export function projectIdFromPath(pathname: string): string | undefined {
  const match = /^\/projects\/([^/?#]+)\/?$/.exec(pathname || '');
  if (!match) return undefined;
  try { return decodeURIComponent(match[1]); } catch { return match[1]; }
}

export function readBrowserRoute(): BrowserRoute {
  return {
    pathname: window.location.pathname || '/',
    search: window.location.search || '',
    hash: window.location.hash || '',
  };
}

export function routeHref(route: BrowserRoute): string {
  return `${route.pathname || '/'}${route.search || ''}${route.hash || ''}`;
}

export function safeReturnPath(value: string | null | undefined, fallback = '/'): string {
  if (!value) return fallback;
  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin) return fallback;
    if (!target.pathname.startsWith('/') || target.pathname.startsWith('//')) return fallback;
    if (target.pathname === LOGIN_PATH) return fallback;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return fallback;
  }
}

export function returnPathFromLoginSearch(search: string): string {
  const params = new URLSearchParams(search);
  return safeReturnPath(params.get('redirect'));
}

export function buildLoginHref(returnTo: string, reason?: 'required' | 'expired' | 'logout' | 'offline'): string {
  const params = new URLSearchParams();
  const safeTarget = safeReturnPath(returnTo);
  if (safeTarget !== '/') params.set('redirect', safeTarget);
  if (reason) params.set('reason', reason);
  const query = params.toString();
  return query ? `${LOGIN_PATH}?${query}` : LOGIN_PATH;
}

export function loginReasonMessage(search: string): string | undefined {
  const reason = new URLSearchParams(search).get('reason');
  if (reason === 'expired') return '登录会话已过期，请重新登录后继续。';
  if (reason === 'logout') return '已安全退出系统。';
  if (reason === 'offline') return '暂时无法验证登录状态，请检查后端服务。';
  return undefined;
}
