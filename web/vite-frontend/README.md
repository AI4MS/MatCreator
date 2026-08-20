# MatCreator frontend

This directory contains the Vite browser application. Use the Node version in
`.nvmrc` and keep `package-lock.json` committed so local, CI, and Docker builds
resolve the same dependency tree.

## Architecture boundaries

- `src/main.js` is the composition root. Keep it focused on creating features,
  passing dependencies, and wiring application-level events.
- `src/features/<feature>/` owns feature-specific UI, state, and controllers.
  A feature should not import another feature's private implementation.
- `src/shared/` contains dependency-free code reused by multiple features,
  including HTTP and DOM utilities. Shared code must not depend on a feature.
- `src/components/` contains reusable visual components; `src/structure/`
  contains the structure data model, workbench, and 3D renderer.
- Put cross-feature behavior behind a small shared API or an injected callback.
  Keep network requests out of view rendering and use `src/shared/api` for JSON
  endpoints so errors, query strings, and empty responses behave consistently.

## Security and DOM conventions

- Treat API data, user input, Markdown, filenames, and URLs as untrusted.
- Prefer `textContent`, DOM properties, and `createElement`. Do not interpolate
  untrusted values into `innerHTML`; HTML-producing renderers must sanitize at
  their boundary before insertion.
- Construct query strings with `URLSearchParams` or the shared HTTP client.
  Do not concatenate unescaped values into URLs or CSS selectors.
- Validate protocols before assigning external links and avoid putting secrets
  or long-lived credentials in browser storage.
- Show actionable errors to users where recovery is possible. A deliberately
  quiet failure should be documented at its call site.

## Controller lifecycle

Controllers should receive their DOM elements and collaborators explicitly.
Initialization must be safe to call once, and every listener, observer, timer,
stream, and in-flight request created by a controller must have a matching
cleanup path. Expose a `destroy()` method when a feature owns such resources,
and call it before replacing or remounting the feature. Cleanup should be
idempotent and abort stale asynchronous work so it cannot update detached UI.

## Verification

```bash
nvm use
npm ci
npm test
npm run build
npm run check
```

Tests use Node's built-in test runner and live in `test/`. `npm run check` runs
the unit tests followed by a production build and is the required pre-PR check.
