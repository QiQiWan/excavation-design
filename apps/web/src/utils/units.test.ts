import { formatEngineeringValue, unitLabel, withUnitLabel } from './units';

describe('engineering units', () => {
  it('renders units in headers and values', () => {
    expect(unitLabel('stiffness')).toBe('kN/m');
    expect(withUnitLabel('标高', 'elevation')).toBe('标高（m）');
    expect(formatEngineeringValue(250000, 'stiffness')).toContain('kN/m');
    expect(formatEngineeringValue(undefined, 'force')).toBe('—');
  });
});
