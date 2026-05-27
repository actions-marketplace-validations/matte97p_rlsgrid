# rlsgrid config recipes

rlsgrid reads `pg_catalog` and `information_schema` directly, so it works
against any Postgres database regardless of the ORM or migration tool that
produced the schema. What changes between stacks is the `[tenancy]` block —
how a tenant is identified and how the session is authenticated.

Each recipe below is a starting `rlsgrid.toml`. Adjust `tenant_column` and
`jwt_claims` to match your schema.

## Supabase (JWT, v2)

Modern Supabase stores all claims in a single `request.jwt.claims` GUC.

```toml
[tenancy]
mode = "jwt"
tenant_column = "tenant_id"        # the FK column your child tables carry
user_id_column = "user_id"
auth_function = "auth.uid()"
jwt_shape = "json"
jwt_claims = { sub = "{user_id}", tenant_id = "{tenant_id}", role = "authenticated" }

[exclude]
schemas = ["pg_catalog", "information_schema", "auth", "storage", "graphql", "graphql_public", "extensions", "realtime", "supabase_functions", "vault", "pgsodium"]
```

If your tenant table is keyed on its own PK (e.g. `accounts.id`) and children
reference it via `account_id`, set `tenant_column = "account_id"` — rlsgrid
detects the root table from the FK graph and seeds it first.

## Prisma

Prisma models map to plain tables; multi-tenant Prisma apps usually add a
`tenantId` column (mapped to `tenant_id` via `@map`). Prisma does not create
Postgres roles, so the DB-level split is whatever you configured (often
`authenticated` via a connection role).

```toml
[tenancy]
mode = "jwt"
tenant_column = "tenant_id"
user_id_column = "user_id"
jwt_shape = "json"
jwt_claims = { sub = "{user_id}", tenant_id = "{tenant_id}" }

[roles]
authenticated = "app connection role"
anon = "public"
```

If your Prisma app enforces tenancy in application code rather than RLS,
use `mode = "function"` against your access-check function (below).

## Drizzle

Same shape as Prisma — Drizzle is a query builder over plain tables. Point
`tenant_column` at whatever column your `pgTable` definitions use for the
tenant foreign key.

```toml
[tenancy]
mode = "jwt"
tenant_column = "organization_id"
user_id_column = "user_id"
jwt_shape = "json"
jwt_claims = { sub = "{user_id}", organization_id = "{tenant_id}" }
```

## SQLAlchemy / Alembic

Alembic migrations produce ordinary tables. If you enforce tenancy with RLS
policies that read a session GUC you set per request, mirror that GUC in
`jwt_claims` with `jwt_shape = "individual"`:

```toml
[tenancy]
mode = "jwt"
tenant_column = "tenant_id"
jwt_shape = "individual"
jwt_claims = { "app.current_tenant" = "{tenant_id}", "app.current_user" = "{user_id}" }
```

`jwt_shape = "individual"` writes one GUC per claim
(`set_config('request.jwt.claim.<name>', ...)`); pick the names your policies
actually read.

## Rails / ActiveRecord

If isolation lives in a scope helper rather than RLS — the common Rails
pattern — test the helper directly with function mode:

```toml
[tenancy]
mode = "function"
tenant_column = "account_id"
access_function = "user_can_access_record({user_id}, {row_id})"
```

## Function mode (any stack with an access helper)

When a SQL function is the real gate (e.g. GeoSuite's
`check_user_has_access_to_store`), point rlsgrid at it. Supported
placeholders: `{user_id}`, `{tenant_id}`, `{target_tenant_id}`,
`{target_user_id}`, `{row_id}`, and `{row.<column>}`.

```toml
[tenancy]
mode = "function"
tenant_column = "store_id"
access_function = "check_user_has_access_to_store({user_id}, {row.store_id})"
```

The fuzzer iterates every `(actor, target_row)` pair and asserts the helper
returns false for cross-tenant calls.
