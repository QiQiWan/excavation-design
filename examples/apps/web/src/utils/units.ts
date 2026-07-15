import { UNIT_FIELD_RULES, UNIT_REGISTRY, type GeneratedUnitKey } from '../generated/engineeringUnits';

export type UnitKey = GeneratedUnitKey;

export function unitLabel(key: UnitKey): string {
  return UNIT_REGISTRY[key]?.symbol ?? '—';
}

export function unitDecimals(key: UnitKey): number {
  return Number(UNIT_REGISTRY[key]?.decimals ?? 2);
}

export function inferUnitKey(fieldName: string): UnitKey | undefined {
  for (const rule of UNIT_FIELD_RULES) {
    try {
      if (new RegExp(rule.pattern, 'i').test(fieldName)) return rule.unitKey as UnitKey;
    } catch {
      // Invalid enterprise regex is ignored in the browser; backend validation remains authoritative.
    }
  }
  return undefined;
}

export function withUnitLabel(label: string, key: UnitKey): string {
  const unit = unitLabel(key);
  return unit === '—' ? label : `${label}（${unit}）`;
}

export function withInferredUnitLabel(label: string, fieldName: string): string {
  const key = inferUnitKey(fieldName);
  return key ? withUnitLabel(label, key) : label;
}

export function formatEngineeringValue(value: unknown, key: UnitKey, digits?: number): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  const resolvedDigits = digits ?? unitDecimals(key);
  const text = number.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: resolvedDigits,
  });
  const unit = unitLabel(key);
  return unit === '—' ? text : `${text} ${unit}`;
}

export function formatInferredEngineeringValue(value: unknown, fieldName: string, digits?: number): string {
  const key = inferUnitKey(fieldName);
  if (!key) return value === null || value === undefined || value === '' ? '—' : String(value);
  return formatEngineeringValue(value, key, digits);
}

export function unitBadge(key: UnitKey): string {
  return `[${unitLabel(key)}]`;
}
