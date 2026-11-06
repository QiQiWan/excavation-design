import type { Point2D } from '../types/domain';

export function polygonArea(points: Point2D[]): number {
  if (points.length < 3) return 0;
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    area += a.x * b.y - b.x * a.y;
  }
  return Math.abs(area / 2);
}

export function polygonPerimeter(points: Point2D[]): number {
  if (points.length < 2) return 0;
  let p = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    p += Math.hypot(b.x - a.x, b.y - a.y);
  }
  return p;
}
