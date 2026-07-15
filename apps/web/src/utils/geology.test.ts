import { effectiveGeologicalSurfaces, hasGeologicalSurfacePreview } from './geology';
import type { Project } from '../types/domain';

const surface = {
  id: 'preview-1',
  soilLayerId: 'soil-1',
  grid: {
    xValues: [0, 10],
    yValues: [0, 10],
    zValues: [[-1, -1], [-2, -2]],
  },
};

describe('geological surface working-set projection', () => {
  it('uses the preview when full IDW surfaces are externalized', () => {
    const project = {
      geologicalModel: {
        surfaces: [],
        surfacePreviews: [surface],
      },
    } as unknown as Project;

    expect(effectiveGeologicalSurfaces(project)).toEqual([surface]);
    expect(hasGeologicalSurfacePreview(project)).toBe(true);
  });

  it('prefers full surfaces inside the isolated calculation worker payload', () => {
    const full = { ...surface, id: 'full-1' };
    const project = {
      geologicalModel: {
        surfaces: [full],
        surfacePreviews: [surface],
      },
    } as unknown as Project;

    expect(effectiveGeologicalSurfaces(project)).toEqual([full]);
  });
});
