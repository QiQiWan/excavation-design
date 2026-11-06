export type CandidateXY = { x: number; y: number };

export type SanitizedCandidateGeometry = {
  outline: CandidateXY[];
  supports: Record<string, any>[];
  columns: Record<string, any>[];
  transferBeams: Record<string, any>[];
  transferZones: Record<string, any>[];
  obstacles: Record<string, any>[];
  previewIntegrity: Record<string, any>;
};

function record(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {};
}

function point(value: unknown): CandidateXY | undefined {
  const row = record(value);
  const x = Number(row.x);
  const y = Number(row.y);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : undefined;
}

/**
 * Sanitize candidate geometry before SVG rendering.
 *
 * The API preview contract rejects invalid coordinates, but an in-memory
 * candidate can reach the browser before it has passed through the compact
 * preview cache. Rendering missing values as zero created phantom members at
 * the origin. This guard is shared by every candidate-plan view.
 */
export function sanitizeCandidatePlanGeometry(value: unknown): SanitizedCandidateGeometry {
  const geometry = record(value);
  let invalidMemberCount = 0;
  let invalidPointCount = 0;

  const collectPoints = (values: unknown[], minimum: number): CandidateXY[] => {
    const rows: CandidateXY[] = [];
    for (const item of values) {
      const parsed = point(item);
      if (parsed) rows.push(parsed);
      else invalidPointCount += 1;
    }
    if (rows.length < minimum && values.length) invalidMemberCount += 1;
    return rows;
  };

  const outline = collectPoints(Array.isArray(geometry.outline) ? geometry.outline : [], 3);
  const supports = (Array.isArray(geometry.supports) ? geometry.supports : []).flatMap((value: unknown) => {
    const row = record(value);
    const start = point(row.start);
    const end = point(row.end);
    if (!start || !end) {
      invalidMemberCount += 1;
      if (!start) invalidPointCount += 1;
      if (!end) invalidPointCount += 1;
      return [];
    }
    return [{ ...row, start, end }];
  });
  const columns = (Array.isArray(geometry.columns) ? geometry.columns : []).flatMap((value: unknown) => {
    const row = record(value);
    const location = point(row.location);
    if (!location) {
      invalidMemberCount += 1;
      invalidPointCount += 1;
      return [];
    }
    return [{ ...row, location }];
  });
  const transferBeams = (Array.isArray(geometry.transferBeams) ? geometry.transferBeams : []).flatMap((value: unknown) => {
    const row = record(value);
    const rawPoints = Array.isArray(row.points) ? row.points : Array.isArray(row.axis?.points) ? row.axis.points : [];
    const points = collectPoints(rawPoints, 2);
    return points.length >= 2 ? [{ ...row, points }] : [];
  });
  const transferZones = (Array.isArray(geometry.transferZones) ? geometry.transferZones : []).flatMap((value: unknown) => {
    const row = record(value);
    const zoneOutline = collectPoints(Array.isArray(row.outline) ? row.outline : [], 3);
    return zoneOutline.length >= 3 ? [{ ...row, outline: zoneOutline }] : [];
  });
  const obstacles = (Array.isArray(geometry.obstacles) ? geometry.obstacles : []).flatMap((value: unknown) => {
    const row = record(value);
    const points = collectPoints(Array.isArray(row.points) ? row.points : [], 3);
    return points.length >= 3 ? [{ ...row, points }] : [];
  });

  const upstream = record(geometry.previewIntegrity);
  const upstreamStatus = String(upstream.status ?? 'complete');
  const incomplete = invalidMemberCount > 0 || invalidPointCount > 0;
  const previewIntegrity = {
    ...upstream,
    status: incomplete ? 'incomplete' : upstreamStatus,
    invalidMemberCount: Number(upstream.invalidMemberCount ?? 0) + invalidMemberCount,
    invalidPointCount: Number(upstream.invalidPointCount ?? 0) + invalidPointCount,
    clientSanitized: incomplete,
    message: incomplete
      ? '浏览器已忽略含无效坐标的构件；请重新生成候选并读取完整拓扑。'
      : upstream.message,
  };

  return { outline, supports, columns, transferBeams, transferZones, obstacles, previewIntegrity };
}
