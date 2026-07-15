import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ProjectsPage from './ProjectsPage';

const project = {
  id: 'project-delete',
  name: '待删除项目',
  location: '测试地点',
  createdAt: '2026-07-13T00:00:00Z',
  updatedAt: '2026-07-13T00:00:00Z',
};

beforeEach(() => {
  vi.stubGlobal('confirm', vi.fn(() => true));
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/api/projects') && (!init?.method || init.method === 'GET')) {
      return Promise.resolve(new Response(JSON.stringify([project]), { status: 200 }));
    }
    if (url.endsWith('/api/projects/project-delete') && init?.method === 'DELETE') {
      return Promise.resolve(new Response(JSON.stringify({
        deleted: true,
        projectId: project.id,
        projectName: project.name,
        deletedTaskCount: 0,
        deletedArtifactCount: 0,
      }), { status: 200 }));
    }
    return Promise.resolve(new Response('{}', { status: 200 }));
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ProjectsPage delete', () => {
  it('deletes a project after explicit confirmation', async () => {
    render(<ProjectsPage onOpen={vi.fn()} />);
    expect(await screen.findByText('待删除项目')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.queryByText('待删除项目')).not.toBeInTheDocument());
    expect(window.confirm).toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/projects/project-delete'),
      expect.objectContaining({ method: 'DELETE' }),
    );
  });
});
