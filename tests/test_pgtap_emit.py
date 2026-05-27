"""Unit tests for the pgTAP emitter, including CONDITIONAL coverage."""

from __future__ import annotations

from rlsgrid.config import TenancyConfig
from rlsgrid.emitters.pgtap import emit
from rlsgrid.introspect import IntrospectionResult
from rlsgrid.matrix import Expected, MatrixCell


def _intro_all_granted(*roles: str, table: str = "posts", schema: str = "public") -> IntrospectionResult:
    grants = {
        (r, schema, table, op)
        for r in roles
        for op in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    intro = IntrospectionResult(grants=grants)
    intro.primary_keys = {(schema, table): ("id",)}
    return intro


def _cell(role: str, op: str, expected: Expected, table: str = "posts") -> MatrixCell:
    return MatrixCell(
        role=role,
        role_purpose="test",
        schema="public",
        table=table,
        operation=op,
        expected=expected,
        applicable_policies=("owner_policy",) if expected is Expected.CONDITIONAL else (),
    )


def test_emit_allow_deny_only_when_no_state() -> None:
    cells = [
        _cell("authenticated", "SELECT", Expected.CONDITIONAL),
        _cell("anon", "SELECT", Expected.DENY),
        _cell("authenticated", "SELECT", Expected.ALLOW),
    ]
    sql = emit(cells)
    assert "BEGIN;" in sql
    assert "SELECT plan(2);" in sql  # ALLOW + DENY only
    assert "CONDITIONAL" not in sql.splitlines()[0]  # header note absent


def test_emit_includes_conditional_with_seed_state() -> None:
    cells = [_cell("authenticated", "SELECT", Expected.CONDITIONAL)]
    state = {
        "tenant_column": "author_id",
        "tenants": [
            {
                "tenant_id": "tenant-A",
                "user_id": "user-A",
                "rows_per_table": {"public.posts": [{"id": "row-A"}]},
            },
            {
                "tenant_id": "tenant-B",
                "user_id": "user-B",
                "rows_per_table": {"public.posts": [{"id": "row-B"}]},
            },
        ],
    }
    tenancy = TenancyConfig(tenant_column="author_id")
    sql = emit(cells, seed_state=state, tenancy=tenancy)
    assert 'WHERE "id" = \'row-B\'' in sql  # target identified by seeded PK
    assert "set_config('request.jwt.claims'" in sql
    assert "user-A" in sql  # actor user id rendered into claim
    assert "SELECT plan(1);" in sql


def test_emit_conditional_insert_is_excluded() -> None:
    # Cross-tenant INSERT is covered by the runtime fuzz, not static pgTAP.
    cells = [_cell("authenticated", "INSERT", Expected.CONDITIONAL)]
    state = {
        "tenant_column": "author_id",
        "tenants": [
            {"tenant_id": "a", "user_id": "u-a", "rows_per_table": {"public.posts": [{"id": "1"}]}},
            {"tenant_id": "b", "user_id": "u-b", "rows_per_table": {"public.posts": [{"id": "2"}]}},
        ],
    }
    sql = emit(cells, seed_state=state, tenancy=TenancyConfig(tenant_column="author_id"))
    assert "SELECT plan(0);" in sql


def test_base_deny_select_without_grant_throws() -> None:
    cells = [_cell("anon", "SELECT", Expected.DENY)]
    intro = IntrospectionResult()  # no grants → privilege deny
    sql = emit(cells, introspection=intro)
    assert "throws_ok" in sql
    assert "'42501'" in sql
    assert "no privilege" in sql


def test_base_deny_select_with_grant_asserts_zero_rows() -> None:
    cells = [_cell("anon", "SELECT", Expected.DENY)]
    intro = _intro_all_granted("anon")
    sql = emit(cells, introspection=intro)
    assert "count(*)" in sql
    assert "0::bigint" in sql
    assert "RLS denies" in sql


def test_base_update_never_uses_ctid() -> None:
    cells = [_cell("anon", "UPDATE", Expected.DENY)]
    intro = IntrospectionResult()  # no grant → privilege probe
    intro.primary_keys = {("public", "posts"): ("id",)}
    sql = emit(cells, introspection=intro)
    assert "ctid" not in sql
    assert 'SET "id" = "id"' in sql
    assert "throws_ok" in sql


def test_base_granted_write_ops_skipped() -> None:
    # Granted UPDATE/DELETE at the base layer produce nothing — they belong to
    # the CONDITIONAL + fuzz layers.
    cells = [
        _cell("authenticated", "UPDATE", Expected.CONDITIONAL),
        _cell("authenticated", "DELETE", Expected.CONDITIONAL),
    ]
    intro = _intro_all_granted("authenticated")
    sql = emit(cells, introspection=intro)
    assert "SELECT plan(0);" in sql


def test_emit_conditional_delete_skipped_when_no_target_rows() -> None:
    cells = [_cell("authenticated", "DELETE", Expected.CONDITIONAL)]
    state = {
        "tenant_column": "author_id",
        "tenants": [
            {"tenant_id": "a", "user_id": "u-a", "rows_per_table": {}},
            {"tenant_id": "b", "user_id": "u-b", "rows_per_table": {}},
        ],
    }
    sql = emit(cells, seed_state=state, tenancy=TenancyConfig(tenant_column="author_id"))
    assert "DELETE" not in sql.upper().replace("DELETED", "")  # no DELETE statement emitted
    assert "SELECT plan(0);" in sql


def test_emit_skips_conditional_for_bypass_roles() -> None:
    cells = [_cell("service_role", "SELECT", Expected.CONDITIONAL)]
    state = {
        "tenant_column": "author_id",
        "tenants": [
            {"tenant_id": "a", "user_id": "u-a", "rows_per_table": {"public.posts": [{"id": "1"}]}},
            {"tenant_id": "b", "user_id": "u-b", "rows_per_table": {"public.posts": [{"id": "2"}]}},
        ],
    }
    sql = emit(cells, seed_state=state, tenancy=TenancyConfig(tenant_column="author_id"))
    assert "service_role" not in sql
    assert "SELECT plan(0);" in sql
