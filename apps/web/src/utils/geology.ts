import type { GeologicalSurface, Project } from '../types/domain';

export function effectiveGeologicalSurfaces(project: Project): GeologicalSurface[] {
  const model = project.geologicalModel;
  if (!model) return [];
  return model.surfaces?.length ? model.surfaces : (model.surfacePreviews ?? []);
}

export function hasGeologicalSurfacePreview(project: Project): boolean {
  return effectiveGeologicalSurfaces(project).length > 0;
}
