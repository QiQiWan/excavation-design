import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});

describe('application login routing', () => {
  it('redirects an unauthenticated protected URL to /login and returns after login', async () => {
    window.history.replaceState({}, '', '/docs');
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/api/auth/status')) {
        return Promise.resolve(new Response(JSON.stringify({ loginRequired: true, mode: 'session_login_and_api_key_rbac', sessionTtlSeconds: 28800 }), { status: 200 }));
      }
      if (url.endsWith('/api/auth/me')) {
        return Promise.resolve(new Response(JSON.stringify({ detail: '未登录或会话已过期' }), { status: 401 }));
      }
      if (url.endsWith('/api/auth/login') && init?.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify({
          authenticated: true,
          identity: { actor: 'engineer', username: 'engineer', role: 'designer', authenticated: true, authMode: 'session' },
          expiresInSeconds: 28800,
        }), { status: 200 }));
      }
      if (url.endsWith('/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'ok', service: 'pitguard-api' }), { status: 200 }));
      if (url.endsWith('/api/system/diagnostics')) return Promise.resolve(new Response(JSON.stringify({ version: '3.24.1', pythonVersion: '3.11', missingModules: [], modules: [] }), { status: 200 }));
      if (url.endsWith('/api/documentation')) return Promise.resolve(new Response(JSON.stringify({ title: 'PitGuard 操作文档', version: '3.24.1', chapters: [], standardsMatrix: { steps: [], catalog: [], precedence: [] }, calculationPrinciples: [], fileGuide: [] }), { status: 200 }));
      return Promise.resolve(new Response('{}', { status: 200 }));
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: '登录系统' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(new URLSearchParams(window.location.search).get('redirect')).toBe('/docs');

    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'engineer' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: '登录并进入工作台' }));

    await waitFor(() => expect(window.location.pathname).toBe('/docs'));
    expect(await screen.findByText('PitGuard 操作文档')).toBeInTheDocument();
  });

  it('redirects to the login route when a session expires during use', async () => {
    window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.endsWith('/api/auth/status')) return Promise.resolve(new Response(JSON.stringify({ loginRequired: true, mode: 'session', sessionTtlSeconds: 28800 }), { status: 200 }));
      if (url.endsWith('/api/auth/me')) return Promise.resolve(new Response(JSON.stringify({ authenticated: true, identity: { actor: 'engineer', username: 'engineer', role: 'designer', authenticated: true, authMode: 'session' } }), { status: 200 }));
      if (url.endsWith('/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'ok', service: 'pitguard-api' }), { status: 200 }));
      if (url.endsWith('/api/system/diagnostics')) return Promise.resolve(new Response(JSON.stringify({ version: '3.24.1', pythonVersion: '3.11', missingModules: [], modules: [] }), { status: 200 }));
      if (url.endsWith('/api/projects')) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      return Promise.resolve(new Response('{}', { status: 200 }));
    }));

    render(<App />);
    expect(await screen.findByText('项目列表')).toBeInTheDocument();

    act(() => { window.dispatchEvent(new CustomEvent('pitguard:unauthorized')); });
    expect(await screen.findByRole('heading', { name: '登录系统' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(screen.getByText('登录会话已过期，请重新登录后继续。')).toBeInTheDocument();
  });
});

describe('API outage resilience', () => {
  it('shows a retryable application login page when auth status cannot be reached', async () => {
    window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.endsWith('/api/auth/status')) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve(new Response('{}', { status: 200 }));
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: '登录系统' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(screen.getByText('登录服务暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新检测服务' })).toBeInTheDocument();
  });
});
