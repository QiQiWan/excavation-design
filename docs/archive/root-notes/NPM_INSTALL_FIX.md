# NPM install fix

The frontend lockfile in earlier generated packages contained tarball URLs pointing to an internal OpenAI package proxy. On a local machine this causes `npm ci` to fail with errors such as:

```text
connect ETIMEDOUT ... packages.applied-caas-gateway1.internal.api.openai.org
```

This package fixes the lockfile by rewriting all `resolved` tarball URLs to the public npm registry and adds `apps/web/.npmrc`:

```text
registry=https://registry.npmjs.org/
```

Use the following commands from `apps/web`:

```bash
npm cache clean --force
npm config set registry https://registry.npmjs.org/
npm ci --registry=https://registry.npmjs.org/
npm test -- --run
npm run build
```

If `vitest` or `tsc` is reported as not found, `npm ci` did not finish successfully, so `node_modules/.bin` has not been created yet.
