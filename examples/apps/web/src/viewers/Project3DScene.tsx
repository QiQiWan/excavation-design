import type { Project } from '../types/domain';
import ProjectSceneViewer from './ProjectSceneViewer';

export default function Project3DScene({ project, focus = 'all' }: { project: Project; focus?: 'all' | 'geology' | 'retaining' | 'results' }) {
  return <ProjectSceneViewer project={project} mode={focus} />;
}
