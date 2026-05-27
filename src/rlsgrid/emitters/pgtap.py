"""Emit a pgTAP test suite from a matrix.

The emitted SQL runs with `pg_prove` or the pgxn runner inside the database
(or a freshly seeded clone).

Two layers of coverage:

**Base ALLOW/DENY probes** — derived from the matrix + table privileges, and
careful to assert what Postgres actually does:

- A role with no grant for an operation raises `42501` (insufficient
  privilege) → `throws_ok`.
- A granted role with a permissive, un-gated policy can run a SELECT →
  `lives_ok`.
- A granted role with RLS enabled but *no* matching policy sees zero rows on
  SELECT (RLS is silent — it does not raise) → `is(count, 0)`.
- For INSERT/UPDATE/DELETE only the no-privilege case is asserted at this
  layer (the granted cases need real per-tenant rows; that is the
  CONDITIONAL layer + the runtime fuzz).

**CONDITIONAL probes** — only when `seed_state` is supplied. They assert the
real cross-tenant property using the seeded UUIDs: the actor must not SELECT,
UPDATE, or DELETE the target tenant's rows. (Cross-tenant INSERT is covered by
the runtime fuzz, which can build a fully valid row.)
"""

from __future__ import annotations

import json
from collections import defaultdict
from io import StringIO
from typing import Any

from ..config import TenancyConfig
from ..introspect import IntrospectionResult
from ..matrix import Expected, MatrixCell


def emit(
    cells: list[MatrixCell],
    *,
    header_note: str | None = None,
    seed_state: dict[str, Any] | None = None,
    tenancy: TenancyConfig | None = None,
    introspection: IntrospectionResult | None = None,
) -> str:
    """Render a complete pgTAP script."""
    base_by_table: dict[str, list[str]] = defaultdict(list)
    base_count = 0
    for cell in cells:
        if cell.expected not in (Expected.ALLOW, Expected.DENY):
            continue
        rendered = _render_base_cell(cell, introspection)
        if rendered is None:
            continue
        base_by_table[cell.qualified_table].append(rendered)
        base_count += 1

    conditional: list[tuple[MatrixCell, dict, dict]] = []
    if seed_state and tenancy:
        conditional = list(_conditional_cells_with_data(cells, seed_state))
    cond_by_table: dict[str, list[tuple[MatrixCell, dict, dict]]] = defaultdict(list)
    for cell, actor, target in conditional:
        cond_by_table[cell.qualified_table].append((cell, actor, target))

    total = base_count + len(conditional)

    out = StringIO()
    out.write("-- rlsgrid pgTAP suite — generated, do not edit by hand.\n")
    if header_note:
        out.write(f"-- {header_note}\n")
    if conditional:
        out.write(
            f"-- CONDITIONAL coverage uses seeded tenants from state file "
            f"({len(conditional)} cells).\n"
        )
    out.write("BEGIN;\n")
    out.write("CREATE EXTENSION IF NOT EXISTS pgtap;\n")
    out.write(f"SELECT plan({total});\n\n")

    for qualified_table in sorted(set(base_by_table) | set(cond_by_table)):
        out.write(f"-- ── {qualified_table} ──\n")
        for probe in base_by_table.get(qualified_table, []):
            out.write(probe)
            out.write("\n")
        for cell, actor, target in cond_by_table.get(qualified_table, []):
            out.write(_render_conditional_cell(cell, actor, target, tenancy))  # type: ignore[arg-type]
            out.write("\n")

    out.write("SELECT * FROM finish();\n")
    out.write("ROLLBACK;\n")
    return out.getvalue()


def _render_base_cell(
    cell: MatrixCell,
    introspection: IntrospectionResult | None,
) -> str | None:
    """Render one ALLOW/DENY probe, or None to skip it.

    Privilege awareness comes from `introspection.has_grant`. When no
    introspection is supplied (unit tests), we assume the role is granted.
    """
    op = cell.operation
    qualified = _quote_qualified(cell.schema, cell.table)
    granted = (
        introspection.has_grant(cell.role, cell.schema, cell.table, op)
        if introspection is not None
        else True
    )

    if op == "SELECT":
        if not granted:
            return _throws(
                cell,
                f"SELECT * FROM {qualified} LIMIT 0",
                f"{cell.role} cannot SELECT {cell.qualified_table} (no privilege)",
            )
        if cell.expected is Expected.ALLOW:
            return _lives(
                cell,
                f"SELECT * FROM {qualified} LIMIT 0",
                f"{cell.role} can SELECT {cell.qualified_table}",
            )
        # DENY + granted: RLS is enabled with no matching policy → zero rows.
        return _is_count_zero(
            cell,
            qualified,
            f"{cell.role} sees no rows on {cell.qualified_table} (RLS denies)",
        )

    # INSERT / UPDATE / DELETE: only the privilege-deny case is cleanly
    # assertable here. Granted write paths need real rows → CONDITIONAL + fuzz.
    if granted:
        return None
    stmt = _privilege_probe_stmt(cell, qualified, introspection)
    if stmt is None:
        return None
    return _throws(
        cell,
        stmt,
        f"{cell.role} cannot {op} {cell.qualified_table} (no privilege)",
    )


def _privilege_probe_stmt(
    cell: MatrixCell,
    qualified: str,
    introspection: IntrospectionResult | None,
) -> str | None:
    """A syntactically valid statement that trips the ACL check before doing work."""
    op = cell.operation
    if op == "INSERT":
        return f"INSERT INTO {qualified} DEFAULT VALUES"
    if op == "DELETE":
        return f"DELETE FROM {qualified} WHERE false"
    if op == "UPDATE":
        pk = introspection.pk_of(cell.schema, cell.table) if introspection else ()
        if not pk:
            return None
        col = _quote_ident(pk[0])
        return f"UPDATE {qualified} SET {col} = {col} WHERE false"
    return None


def _throws(cell: MatrixCell, stmt: str, name: str) -> str:
    return (
        f"SET LOCAL ROLE {_quote_ident(cell.role)};\n"
        f"SELECT throws_ok($rlsgrid${stmt}$rlsgrid$, '42501', NULL, {_quote_literal(name)});\n"
        "RESET ROLE;\n"
    )


def _lives(cell: MatrixCell, stmt: str, name: str) -> str:
    return (
        f"SET LOCAL ROLE {_quote_ident(cell.role)};\n"
        f"SELECT lives_ok($rlsgrid${stmt}$rlsgrid$, {_quote_literal(name)});\n"
        "RESET ROLE;\n"
    )


def _is_count_zero(cell: MatrixCell, qualified: str, name: str) -> str:
    return (
        f"SET LOCAL ROLE {_quote_ident(cell.role)};\n"
        f"SELECT is((SELECT count(*) FROM {qualified}), 0::bigint, {_quote_literal(name)});\n"
        "RESET ROLE;\n"
    )


def _conditional_cells_with_data(
    cells: list[MatrixCell],
    seed_state: dict[str, Any],
):
    """Pair each CONDITIONAL cell with two tenants that have data on its table.

    INSERT is intentionally excluded — a valid cross-tenant INSERT needs a
    fully-formed row (FKs, NOT NULL, CHECK), which the runtime fuzz builds but
    is impractical to synthesize in static SQL. SELECT/UPDATE/DELETE only need
    the target's primary key, which the seed state already carries.
    """
    tenants = seed_state.get("tenants", [])
    if len(tenants) < 2:
        return
    for cell in cells:
        if cell.expected is not Expected.CONDITIONAL:
            continue
        if _is_bypass_role(cell.role):
            continue
        if cell.operation == "INSERT":
            continue
        target = tenants[1]
        # Every emitted probe identifies the target's rows by their seeded
        # primary keys, so all of SELECT/UPDATE/DELETE need at least one row.
        if not target.get("rows_per_table", {}).get(cell.qualified_table):
            continue
        yield cell, tenants[0], target


def _render_conditional_cell(
    cell: MatrixCell,
    actor: dict[str, Any],
    target: dict[str, Any],
    tenancy: TenancyConfig,
) -> str:
    qualified = _quote_qualified(cell.schema, cell.table)
    claim_setter = _render_claim_setter(tenancy, actor)
    role_set = f"SET LOCAL ROLE {_quote_ident(cell.role)};\n{claim_setter}\n"

    # Identify the target's rows by their seeded primary keys — works for the
    # tenant root table (keyed by its own PK) and child tables alike, with no
    # assumption that the table carries `tenant_column`.
    target_rows = target.get("rows_per_table", {}).get(cell.qualified_table, [])
    if not target_rows:
        return f"-- skipped {cell.role} {cell.operation} on {cell.qualified_table}: no target rows\n"
    pk_dict = target_rows[0]
    pk_cols = list(pk_dict.keys())
    where = " AND ".join(
        f"{_quote_ident(c)} = {_quote_literal(str(pk_dict[c]))}" for c in pk_cols
    )

    if cell.operation == "SELECT":
        test_name = _quote_literal(
            f"{cell.role} as actor cannot SELECT target rows on {cell.qualified_table}"
        )
        return (
            role_set
            + "SELECT is(\n"
            + f"  (SELECT count(*) FROM {qualified} WHERE {where}),\n"
            + "  0::bigint,\n"
            + f"  {test_name}\n"
            + ");\n"
            + "RESET ROLE;\n"
        )

    if cell.operation == "UPDATE":
        set_clause = ", ".join(f"{_quote_ident(c)} = {_quote_ident(c)}" for c in pk_cols)
        cte = f"UPDATE {qualified} SET {set_clause} WHERE {where} RETURNING 1"
        test_name = _quote_literal(
            f"{cell.role} as actor cannot UPDATE target-owned row on {cell.qualified_table}"
        )
    else:  # DELETE
        cte = f"DELETE FROM {qualified} WHERE {where} RETURNING 1"
        test_name = _quote_literal(
            f"{cell.role} as actor cannot DELETE target-owned row on {cell.qualified_table}"
        )
    return (
        role_set
        + f"WITH affected AS ({cte})\n"
        + f"SELECT is((SELECT count(*) FROM affected), 0::bigint, {test_name});\n"
        + "RESET ROLE;\n"
    )


def _render_claim_setter(tenancy: TenancyConfig, actor: dict[str, Any]) -> str:
    rendered = {
        name: template.format(user_id=actor["user_id"], tenant_id=actor["tenant_id"])
        for name, template in tenancy.jwt_claims.items()
    }
    if tenancy.jwt_shape == "json":
        return (
            "SELECT set_config('request.jwt.claims', "
            f"{_quote_literal(json.dumps(rendered))}, true);"
        )
    return "\n".join(
        f"SELECT set_config('request.jwt.claim.{name}', {_quote_literal(value)}, true);"
        for name, value in rendered.items()
    )


_BYPASS_ROLES = {"service_role", "postgres", "supabase_admin"}


def _is_bypass_role(role: str) -> bool:
    return role in _BYPASS_ROLES


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_qualified(schema: str, table: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(table)}"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
