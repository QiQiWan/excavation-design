import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const web = resolve(root, 'apps', 'web');
const metadata = JSON.parse(readFileSync(resolve(web, 'package.json'), 'utf8'));
const target = resolve(web, 'public', 'pitguard-version.json');
mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, `${JSON.stringify({ product: 'PitGuard', uiVersion: String(metadata.version) })}\n`, 'utf8');
console.log(`[PitGuard] generated ${target}`);
