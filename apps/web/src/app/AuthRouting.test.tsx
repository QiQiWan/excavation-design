import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

// 模拟真实后端：必须带 JSON Content-Type，否则客户端按文本解析，
// loginRequired 读不到会让应用误判成本地模式（这是本文件此前失败的原因）。
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});

describe('application login routing', () => {
  it('redirects an unauthenticated protected URL to /login and returns after login', async () => {
    window.history.replaceState({}, '', '/projects/demo');
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/api/auth/status')) {
        return Promise.resolve(json({ loginRequired: true, mode: 'session_login_and_api_key_rbac', sessionTtlSeconds: 28800 }));
      }
      if (url.endsWith('/api/auth/me')) {
        return Promise.resolve(json({ detail: '未登录或会话已过期' }, 401));
      }
      if (url.endsWith('/api/auth/login') && init?.method === 'POST') {
        return Promise.resolve(json({
          authenticated: true,
          identity: { actor: 'engineer', username: 'engineer', role: 'designer', authenticated: true, authMode: 'session' },
          expiresInSeconds: 28800,
        }));
      }
      if (url.endsWith('/health')) return Promise.resolve(json({ status: 'ok', service: 'pitguard-api' }));
      if (url.endsWith('/api/system/diagnostics')) return Promise.resolve(json({ version: '3.24.1', pythonVersion: '3.11', missingModules: [], modules: [] }));
      if (url.endsWith('/api/documentation')) return Promise.resolve(json({ title: 'PitGuard 操作文档', version: '3.24.1', chapters: [], standardsMatrix: { steps: [], catalog: [], precedence: [] }, calculationPrinciples: [], fileGuide: [] }));
      if (url.includes('/api/projects')) return Promise.resolve(json([]));
      return Promise.resolve(json({}));
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: '登录系统' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(new URLSearchParams(window.location.search).get('redirect')).toBe('/projects/demo');

    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'engineer' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: '登录并进入工作台' }));

    await waitFor(() => expect(window.location.pathname).toBe('/projects/demo'));
    // 回到受保护路由后不再停留在登录页（工程数据由后续接口恢复）。
    await waitFor(() => expect(screen.queryByRole('heading', { name: '登录系统' })).not.toBeInTheDocument());
  });

  it('redirects to the login route when a session expires during use', async () => {
    window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.endsWith('/api/auth/status')) return Promise.resolve(json({ loginRequired: true, mode: 'session', sessionTtlSeconds: 28800 }));
      if (url.endsWith('/api/auth/me')) return Promise.resolve(json({ authenticated: true, identity: { actor: 'engineer', username: 'engineer', role: 'designer', authenticated: true, authMode: 'session' } }));
      if (url.endsWith('/health')) return Promise.resolve(json({ status: 'ok', service: 'pitguard-api' }));
      if (url.endsWith('/api/system/diagnostics')) return Promise.resolve(json({ version: '3.24.1', pythonVersion: '3.11', missingModules: [], modules: [] }));
      if (url.endsWith('/api/projects')) return Promise.resolve(json([]));
      return Promise.resolve(json({}));
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
  it('fails closed when the auth response is not a JSON object', async () => {
    window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      // 模拟代理把 JSON 改成 text/plain：loginRequired 读不到，绝不能当本地模式放行。
      if (url.endsWith('/api/auth/status')) return Promise.resolve(new Response(JSON.stringify({ loginRequired: false }), { status: 200 }));
      return Promise.resolve(json({}));
    }));

    render(<App />);

    expect(await screen.findByText('登录服务暂不可用')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(screen.queryByText('项目列表')).not.toBeInTheDocument();
  });

  it('shows a retryable application login page when auth status cannot be reached', async () => {
    window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.endsWith('/api/auth/status')) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve(json({}));
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: '登录系统' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(screen.getByText('登录服务暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新检测服务' })).toBeInTheDocument();
  });
});
