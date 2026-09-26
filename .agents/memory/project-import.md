---
name: Imported repository startup
description: Imported repositories need an explicit root workflow pointing at their real service directories after project creation.
---

For a GitHub repository moved into a Replit project, the durable root Run workflow must point directly to the repository's actual service directories. Generated artifact workflows may retain stale paths from the bootstrap workspace and should not be treated as the app's source of truth.

**Why:** Project creation can add a new monorepo scaffold and regenerate workflow entries, while the imported repository keeps its own runtime layout.

**How to apply:** After importing an existing repository, verify the root Run workflow and start the services from their real source directories before considering the project ready.