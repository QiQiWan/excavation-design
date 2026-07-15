# Backend default port update

The default backend API port is now `8002`.

Updated components:

- `start-linux.sh`
- `start-windows.ps1` and `start-windows.bat`
- manual development command in `scripts/dev.sh`
- frontend API fallback in `apps/web/src/api/client.ts`
- root README and deployment documentation

The port remains configurable through `PITGUARD_BACKEND_PORT`. For example:

```bash
PITGUARD_BACKEND_PORT=8010 bash start-linux.sh
```

The frontend startup scripts inject `VITE_API_BASE_URL=http://127.0.0.1:<backend-port>`, so custom backend ports remain synchronized automatically.

## Verification

- Linux shell syntax: passed.
- Backend health check on `http://127.0.0.1:8002/health`: passed.
- Frontend tests: 6 files and 8 tests passed.
- Frontend production build with `VITE_API_BASE_URL=http://127.0.0.1:8002`: passed.
