import type { Project } from '../types/domain';
import ProjectSceneViewer from './ProjectSceneViewer';

export default function ThreeModelViewer({ project, mode = 'combined' }: { project: Project; mode?: 'geology' | 'retaining' | 'results' | 'combined' }) {
  const mapped = mode === 'combined' ? 'all' : mode;
  return <ProjectSceneViewer project={project} mode={mapped} />;
}
