import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'packages', 'units', 'engineering-units.json');
const target = path.join(root, 'apps', 'web', 'src', 'generated', 'engineeringUnits.ts');
const data = JSON.parse(fs.readFileSync(source, 'utf8'));
const aliases = data.aliases || {};
const quantityKeys = Object.keys(data.quantities);
const publicKeys = [...quantityKeys, ...Object.keys(aliases)];
const registry = {};
for (const key of quantityKeys) registry[key] = data.quantities[key];
for (const [alias, canonical] of Object.entries(aliases)) registry[alias] = data.quantities[canonical];
const sourceText = `// Generated from packages/units/engineering-units.json. Do not edit manually.\n` +
  `export const UNIT_SYSTEM = ${JSON.stringify(data.system)} as const;\n` +
  `export const UNIT_RULES = ${JSON.stringify(data.rules, null, 2)} as const;\n` +
  `export const UNIT_REGISTRY = ${JSON.stringify(registry, null, 2)} as const;\n` +
  `export const UNIT_FIELD_RULES = ${JSON.stringify(data.fieldRules || [], null, 2)} as const;\n` +
  `export type GeneratedUnitKey = ${publicKeys.map((key) => JSON.stringify(key)).join(' | ')};\n`;
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.writeFileSync(target, sourceText, 'utf8');
console.log(`[PitGuard] generated ${path.relative(root, target)}`);
