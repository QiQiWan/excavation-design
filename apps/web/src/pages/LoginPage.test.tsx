import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import LoginPage from './LoginPage';

afterEach(() => vi.unstubAllGlobals());

describe('LoginPage', () => {
  it('uses the application form and returns the authenticated identity', async () => {
    const onAuthenticated = vi.fn();
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/api/auth/login') && init?.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify({
          authenticated: true,
          identity: { actor: 'engineer', username: 'engineer', role: 'designer', authenticated: true, authMode: 'session' },
          expiresInSeconds: 28800,
        }), { status: 200 }));
      }
      return Promise.resolve(new Response('{}', { status: 404 }));
    }));

    render(<LoginPage onAuthenticated={onAuthenticated} />);
    expect(screen.getByRole('heading', { name: '登录 PitGuard' })).toBeInTheDocument();
    expect(screen.queryByText(/Basic Auth/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'engineer' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(expect.objectContaining({ role: 'designer', authMode: 'session' })));
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/login'),
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    );
  });
});
