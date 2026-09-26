---
name: Dashboard dependency install
description: The dashboard can be installed independently when a workspace-wide pnpm install hits an unrelated generator dependency.
---

When the React dashboard is missing its local Vite binary after an import, install it with the dashboard workspace filter instead of forcing a full workspace install.

**Why:** The full lockfile install can be blocked by an unrelated API-codegen package, while the dashboard's own dependency graph is sufficient to run its preview.

**How to apply:** Use `pnpm install --filter @workspace/dashboard --frozen-lockfile`, then restart the managed `artifacts/dashboard: web` workflow.