import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ProjectDataWorkspacePanel from './ProjectDataWorkspacePanel';
import type { Project } from '../types/domain';

const project = {
  id: 'workspace-project', name: 'workspace', createdAt: '', updatedAt: '2026-07-16',
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: {}, boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
} as unknown as Project;

it('loads only storage health initially and defers the artifact manifest until expanded', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/storage-health')) return new Response(JSON.stringify({ workspaceBytes: 1024, workspaceLimitBytes: 4096, payloadBytes: 2048, apiFullLoadLimitBytes: 8192, fullLoadAllowed: true, workspaceLoadAllowed: true }), { status: 200 });
    if (url.includes('/artifacts')) return new Response(JSON.stringify({ projectId: project.id, artifactCount: 1, storedBytes: 128, logicalBytes: 512, artifacts: [{ artifactId: 'a1', kind: 'calculation-stage-results', storedBytes: 128, logicalBytes: 512 }] }), { status: 200 });
    throw new Error(`unexpected URL ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ProjectDataWorkspacePanel project={project} />);
  await waitFor(() => expect(screen.getByText(/运行模式/)).toBeInTheDocument());
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/artifacts'))).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '查看数据索引' }));
  await waitFor(() => expect(screen.getByText('calculation-stage-results')).toBeInTheDocument());
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/artifacts'))).toBe(true);
});
