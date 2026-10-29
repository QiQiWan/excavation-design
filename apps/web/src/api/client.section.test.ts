import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, invalidateApiCache } from './client';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); invalidateApiCache(); });

describe('frozen section API client', () => {
  it('freezes the full input and analyzes only the returned snapshot URL with session credentials', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ snapshotHash: 'server-snapshot' }), { headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ resultHash: 'server-result' }), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetch);
    const input = { schema_version: 'pitguard.frozen-section/1', section_id: 'TEST' };
    await api.freezeSection(input);
    await api.analyzeSectionSnapshot('server-snapshot');
    expect(fetch.mock.calls[0][0]).toBe('/api/sections/freeze');
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'include', body: JSON.stringify(input) });
    expect(fetch.mock.calls[1][0]).toBe('/api/sections/snapshots/server-snapshot/analyze');
    expect(fetch.mock.calls[1][1]).toMatchObject({ method: 'POST', credentials: 'include' });
    expect(fetch.mock.calls[1][1].body).toBeUndefined();
  });

  it('downloads ZIP bytes without decoding them as text through the same authenticated client', async () => {
    const bytes = new Uint8Array([80, 75, 3, 4, 0, 255, 128, 196]);
    const fetch = vi.fn().mockResolvedValue(new Response(bytes, { headers: { 'Content-Type': 'application/zip' } }));
    vi.stubGlobal('fetch', fetch);
    const blob = await api.downloadSectionPackage('stored-result');
    expect(fetch.mock.calls[0][0]).toBe('/api/sections/results/stored-result/package');
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('application/zip');
    const reader = new FileReader();
    const buffer = await new Promise<ArrayBuffer>((resolve) => { reader.onload = () => resolve(reader.result as ArrayBuffer); reader.readAsArrayBuffer(blob); });
    expect(new Uint8Array(buffer)).toEqual(bytes);
  });

  it('keeps structured server errors and unauthorized events on a failed package download', async () => {
    const listener = vi.fn();
    window.addEventListener('pitguard:unauthorized', listener);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '请登录后下载' }), {
      status: 401, headers: { 'Content-Type': 'application/json' },
    })));
    await expect(api.downloadSectionPackage('protected-result')).rejects.toThrow('请登录后下载');
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener('pitguard:unauthorized', listener);
  });

  it('shows the field and reason from frozen-input validation failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [
      { loc: ['body', 'pile', 'length_m'], msg: 'Input should be greater than 0', type: 'greater_than' },
      { loc: ['body'], msg: 'Value error, 土层必须覆盖桩底', type: 'value_error' },
    ] }), { status: 422, headers: { 'Content-Type': 'application/json' } })));
    await expect(api.freezeSection({})).rejects.toThrow(/pile\.length_m.*greater than 0.*土层必须覆盖桩底/s);
  });

  it('allows a section analysis to run for 180 seconds before reporting a recoverable timeout', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    })));
    const settled = vi.fn();
    const request = api.analyzeSectionSnapshot('slow-snapshot').catch((error: Error) => { settled(error.message); });
    await vi.advanceTimersByTimeAsync(179999);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await request;
    expect(settled).toHaveBeenCalledWith(expect.stringContaining('180 秒'));
    expect(settled).toHaveBeenCalledWith(expect.stringContaining('计算可能仍在执行，可恢复该冻结输入最近已保存结果'));
  });

  it('retrieves the latest saved result by snapshot with a read-only authenticated request', async () => {
    const saved = { resultHash: 'stored-result' };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(saved), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetch);
    expect(await api.getLatestSectionResult('server-snapshot')).toEqual(saved);
    expect(fetch.mock.calls[0][0]).toBe('/api/sections/snapshots/server-snapshot/latest-result');
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
    expect(fetch.mock.calls[0][1].method ?? 'GET').toBe('GET');
    expect(fetch.mock.calls[0][1].body).toBeUndefined();
  });

  it('allows 120 seconds for package generation while leaving ordinary section reads at 90 seconds', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    })));
    const packageSettled = vi.fn();
    const readSettled = vi.fn();
    const packageRequest = api.downloadSectionPackage('slow-result').catch((error: Error) => packageSettled(error.message));
    const readRequest = api.getSectionExample().catch((error: Error) => readSettled(error.message));
    await vi.advanceTimersByTimeAsync(90000);
    expect(readSettled).toHaveBeenCalledWith(expect.stringContaining('90 秒'));
    expect(packageSettled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(29999);
    expect(packageSettled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await Promise.all([packageRequest, readRequest]);
    expect(packageSettled).toHaveBeenCalledWith(expect.stringContaining('120 秒'));
  });

  it('preserves cached JSON responses across binary downloads and invalidates them on a mutation', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: 'P1' }]), { headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(new Uint8Array([80, 75, 3, 4]), { headers: { 'Content-Type': 'application/zip' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ snapshotHash: 'frozen' }), { headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: 'P2' }]), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetch);
    expect(await api.listProjects()).toEqual([{ id: 'P1' }]);
    expect(await api.listProjects()).toEqual([{ id: 'P1' }]);
    expect(fetch).toHaveBeenCalledTimes(1);
    await api.downloadSectionPackage('binary-result');
    expect(await api.listProjects()).toEqual([{ id: 'P1' }]);
    expect(fetch).toHaveBeenCalledTimes(2);
    await api.freezeSection({});
    expect(await api.listProjects()).toEqual([{ id: 'P2' }]);
    expect(fetch).toHaveBeenCalledTimes(4);
  });
});


it('uses explicit reference endpoints, payloads and result-bound curve routes', async () => {
  const fetch=vi.fn().mockImplementation(() => Promise.resolve(new Response('{}', { headers: { 'Content-Type': 'application/json' } })));
  vi.stubGlobal('fetch',fetch);
  await api.getSectionExampleV2(); await api.getA3Reference();
  await api.saveSectionReferenceContract({ parameters: [] });
  await api.saveSectionReferenceCurves({ stages: [] });
  await api.compareSectionReference('result/a', { contract_hash: 'contract', curve_bundle_hash: 'bundle' });
  await api.getSectionCurves('result/a');
  expect(fetch.mock.calls.map(call => call[0])).toEqual(['/api/sections/example-v2','/api/sections/reference/a3-3','/api/sections/reference/contracts','/api/sections/reference/curves','/api/sections/results/result%2Fa/compare-reference','/api/sections/results/result%2Fa/curves']);
  expect(fetch.mock.calls[4][1]).toMatchObject({ method:'POST', credentials:'include', body: JSON.stringify({ contract_hash:'contract', curve_bundle_hash:'bundle' }) });
});
