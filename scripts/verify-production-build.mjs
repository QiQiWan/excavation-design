#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const distDir = path.resolve(process.argv[2] || 'apps/web/dist');
if (!fs.existsSync(distDir) || !fs.statSync(distDir).isDirectory()) {
  console.error(`[PitGuard] Production dist directory does not exist: ${distDir}`);
  process.exit(1);
}

const indexPath = path.join(distDir, 'index.html');
if (!fs.existsSync(indexPath)) {
  console.error(`[PitGuard] Production build is incomplete: ${indexPath} is missing.`);
  process.exit(1);
}

const textExtensions = new Set(['.html', '.js', '.mjs', '.css', '.json', '.map']);
const forbidden = [
  { code: 'LOOPBACK_API_IPV4', regex: /https?:\/\/(?:127(?:\.\d{1,3}){3}|0\.0\.0\.0):8002/gi },
  { code: 'LOOPBACK_API_LOCALHOST', regex: /https?:\/\/localhost:8002/gi },
  { code: 'LOOPBACK_API_IPV6', regex: /https?:\/\/\[::1\]:8002/gi },
  { code: 'DUPLICATE_API_PREFIX', regex: /\/api\/api\//g },
];
const findings = [];

function inspect(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  for (const rule of forbidden) {
    rule.regex.lastIndex = 0;
    let match;
    while ((match = rule.regex.exec(content)) !== null) {
      const start = Math.max(0, match.index - 80);
      const end = Math.min(content.length, match.index + match[0].length + 80);
      findings.push({
        code: rule.code,
        file: path.relative(distDir, filePath),
        value: match[0],
        context: content.slice(start, end).replace(/\s+/g, ' '),
      });
      if (findings.length >= 20) return;
    }
  }
}

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(fullPath);
    else if (textExtensions.has(path.extname(entry.name).toLowerCase())) inspect(fullPath);
  }
}

walk(distDir);
if (findings.length) {
  console.error('[PitGuard] Production build verification failed. Unsafe API references were found:');
  for (const item of findings) {
    console.error(`  - [${item.code}] ${item.file}: ${item.value}`);
    console.error(`    ${item.context}`);
  }
  console.error('[PitGuard] Leave VITE_API_BASE_URL empty in production. Client request paths already include /api.');
  process.exit(1);
}

const assetsDir = path.join(distDir, 'assets');
const assetCount = fs.existsSync(assetsDir)
  ? fs.readdirSync(assetsDir).filter((name) => /\.(?:js|css)$/.test(name)).length
  : 0;
if (assetCount === 0) {
  console.error('[PitGuard] Production build verification failed: no JavaScript or CSS assets were emitted.');
  process.exit(1);
}

console.log(`[PitGuard] Production frontend verification: OK (${assetCount} assets, same-origin API mode).`);
