# rlsgrid

Schema-driven Row-Level Security test matrix and cross-tenant fuzzer for
Postgres and Supabase.

```bash
pip install rlsgrid
export DATABASE_URL=postgresql://user:pw@host/db
rlsgrid init --from-db     # read schema, write config
rlsgrid check --tenants 5  # seed → fuzz → teardown, exit 1 on leak
```

- **What it does** — reads your live schema, classifies every
  `role × table × operation`, emits a pgTAP suite, and actively probes for
  cross-tenant SELECT/INSERT/UPDATE/DELETE leaks.
- **Why** — RLS is easy to get subtly wrong, and application unit tests do
  not catch a missing `WITH CHECK` or a `service_role` bypass. rlsgrid does.
- **How** — see the [README on GitHub](https://github.com/matte97p/rlsgrid#readme)
  for the full walkthrough, and [config recipes](RECIPES.md) for your stack.

## Docs

- [Config recipes](RECIPES.md) — Supabase, Prisma, Drizzle, SQLAlchemy, Rails, function mode.
- [Changelog](CHANGELOG.md)
