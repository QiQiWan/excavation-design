# PitGuard V3.87.15 production deployment repair

## Root cause

The previous deployment script searched every generated asset for the literal loopback address. The online documentation fallback contained a server-side health-check command using that address, so a valid same-origin frontend build was rejected after Vite completed.

Backend package metadata also remained at 3.87.12 while the frontend and runtime reported later versions, making deployment status ambiguous.

## Resolution

- Production requests use the current browser origin and `/api` paths.
- Development loopback configuration is isolated in `.env.development`.
- `/api` values are normalized to avoid `/api/api/...` requests.
- A structured verifier reports exact unsafe files and matching context.
- Release metadata is checked before installing dependencies.
- Frontend, lockfile, backend package and runtime versions are synchronized to 3.87.15.

## Production build

```bash
cd apps/web
VITE_API_BASE_URL='' npm run build
node ../../scripts/verify-production-build.mjs dist
```

## Diagnosis

```bash
PYTHON_BIN=/root/miniconda3/envs/ifc/bin/python ./scripts/diagnose-production-api.sh
```
