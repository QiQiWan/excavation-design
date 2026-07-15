import { render, screen } from '@testing-library/react';
import App from './App';

vi.stubGlobal('fetch', vi.fn((url: string) => {
  if (url.endsWith('/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'ok', service: 'pitguard-api' }), { status: 200 }));
  if (url.endsWith('/api/projects')) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
  return Promise.resolve(new Response('{}', { status: 200 }));
}));

describe('App', () => {
  it('renders project list', async () => {
    render(<App />);
    expect(await screen.findByText('项目列表')).toBeInTheDocument();
  });
});
