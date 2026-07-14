import { buildLoginHref, returnPathFromLoginSearch, safeReturnPath } from './navigation';

describe('login route navigation safety', () => {
  it('keeps same-origin protected paths and rejects external redirects', () => {
    expect(safeReturnPath('/docs?chapter=calculation#quality')).toBe('/docs?chapter=calculation#quality');
    expect(safeReturnPath('https://example.com/steal')).toBe('/');
    expect(safeReturnPath('//example.com/steal')).toBe('/');
    expect(safeReturnPath('/login?redirect=/docs')).toBe('/');
  });

  it('builds a login URL and recovers the original protected path', () => {
    const href = buildLoginHref('/docs?chapter=calculation', 'required');
    expect(href.startsWith('/login?')).toBe(true);
    const query = href.slice(href.indexOf('?'));
    expect(returnPathFromLoginSearch(query)).toBe('/docs?chapter=calculation');
    expect(new URLSearchParams(query).get('reason')).toBe('required');
  });
});
