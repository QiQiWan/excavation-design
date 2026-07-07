from __future__ import annotations

import math

from app.schemas.domain import SurfaceGrid


def interpolate_surface_idw(
    points: list[tuple[float, float, float]],
    grid_bounds: tuple[float, float, float, float],
    grid_size: float,
    power: float = 2.0,
) -> SurfaceGrid:
    """Interpolate a regular surface grid by inverse distance weighting."""
    if not points:
        raise ValueError("IDW interpolation requires at least one point")
    min_x, min_y, max_x, max_y = grid_bounds
    if grid_size <= 0:
        raise ValueError("grid_size must be positive")
    nx = max(2, int(math.ceil((max_x - min_x) / grid_size)) + 1)
    ny = max(2, int(math.ceil((max_y - min_y) / grid_size)) + 1)
    x_values = [round(min_x + i * (max_x - min_x) / (nx - 1), 6) for i in range(nx)]
    y_values = [round(min_y + j * (max_y - min_y) / (ny - 1), 6) for j in range(ny)]
    z_values: list[list[float]] = []
    for y in y_values:
        row: list[float] = []
        for x in x_values:
            exact = None
            numerator = 0.0
            denominator = 0.0
            for px, py, pz in points:
                dist = math.hypot(x - px, y - py)
                if dist < 1e-9:
                    exact = pz
                    break
                weight = 1.0 / (dist**power)
                numerator += weight * pz
                denominator += weight
            row.append(round(float(exact if exact is not None else numerator / denominator), 6))
        z_values.append(row)
    return SurfaceGrid(x_values=x_values, y_values=y_values, z_values=z_values)
