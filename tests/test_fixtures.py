"""Unit tests for fixture seeder helpers — no DB required."""

from __future__ import annotations

from rlsgrid.config import Config, ConnectionConfig, TenancyConfig
from rlsgrid.fixtures import (
    _detect_tenant_root,
    _pregenerate_self_reference,
    build_seed_plan,
    topological_sort,
)
from rlsgrid.introspect import (
    ColumnInfo,
    ForeignKeyInfo,
    IntrospectionResult,
    TableInfo,
)


def _table(name: str) -> TableInfo:
    return TableInfo(schema="public", name=name, rls_enabled=True, rls_forced=False)


def _fk(src: str, dst: str) -> ForeignKeyInfo:
    return ForeignKeyInfo(
        schema="public",
        table=src,
        column=f"{dst}_id",
        ref_schema="public",
        ref_table=dst,
        ref_column="id",
    )


def test_topological_sort_orders_parents_first() -> None:
    tables = [_table("posts"), _table("accounts"), _table("comments")]
    intro = IntrospectionResult(
        tables=tables,
        foreign_keys=[_fk("posts", "accounts"), _fk("comments", "posts")],
    )
    ordered = [t.name for t in topological_sort(tables, intro)]
    assert ordered.index("accounts") < ordered.index("posts")
    assert ordered.index("posts") < ordered.index("comments")


def test_topological_sort_breaks_cycles_gracefully() -> None:
    tables = [_table("a"), _table("b")]
    intro = IntrospectionResult(
        tables=tables,
        foreign_keys=[_fk("a", "b"), _fk("b", "a")],
    )
    ordered = topological_sort(tables, intro)
    assert {t.name for t in ordered} == {"a", "b"}


def test_topological_sort_ignores_external_fks() -> None:
    tables = [_table("posts")]
    intro = IntrospectionResult(
        tables=tables,
        foreign_keys=[_fk("posts", "auth_users_external")],
    )
    ordered = topological_sort(tables, intro)
    assert [t.name for t in ordered] == ["posts"]


def test_detect_tenant_root_follows_fk() -> None:
    # projects.org_id → orgs.id : the root is (public, orgs, id)
    intro = IntrospectionResult(
        foreign_keys=[
            ForeignKeyInfo(
                schema="public",
                table="projects",
                column="org_id",
                ref_schema="public",
                ref_table="orgs",
                ref_column="id",
            )
        ]
    )
    assert _detect_tenant_root(intro, "org_id") == ("public", "orgs", "id")


def test_detect_tenant_root_none_when_freestanding() -> None:
    # author_id is a plain value with no FK (the blog pattern).
    intro = IntrospectionResult(foreign_keys=[])
    assert _detect_tenant_root(intro, "author_id") is None


def test_topological_sort_across_schemas() -> None:
    tables = [
        TableInfo(schema="app", name="child", rls_enabled=True, rls_forced=False),
        TableInfo(schema="core", name="parent", rls_enabled=True, rls_forced=False),
    ]
    intro = IntrospectionResult(
        tables=tables,
        foreign_keys=[
            ForeignKeyInfo(
                schema="app",
                table="child",
                column="parent_id",
                ref_schema="core",
                ref_table="parent",
                ref_column="id",
            )
        ],
    )
    ordered = [f"{t.schema}.{t.name}" for t in topological_sort(tables, intro)]
    assert ordered.index("core.parent") < ordered.index("app.child")


def test_topological_sort_ignores_self_reference() -> None:
    tables = [_table("nodes")]
    intro = IntrospectionResult(
        tables=tables,
        foreign_keys=[
            ForeignKeyInfo(
                schema="public",
                table="nodes",
                column="parent_id",
                ref_schema="public",
                ref_table="nodes",
                ref_column="id",
            )
        ],
    )
    ordered = topological_sort(tables, intro)
    assert [t.name for t in ordered] == ["nodes"]


def test_pregenerate_self_reference_uuid_pk() -> None:
    table = TableInfo(schema="public", name="nodes", rls_enabled=True, rls_forced=False)
    columns = [
        ColumnInfo("public", "nodes", "id", "uuid", False, True),
        ColumnInfo("public", "nodes", "parent_id", "uuid", False, False),
    ]
    fks = {
        "parent_id": ForeignKeyInfo(
            schema="public",
            table="nodes",
            column="parent_id",
            ref_schema="public",
            ref_table="nodes",
            ref_column="id",
        )
    }
    pre = _pregenerate_self_reference(table, columns, fks, ("id",))
    # id and the self-FK get the same pre-generated value (row points at itself)
    assert pre["id"] == pre["parent_id"]
    assert pre["id"]


def test_pregenerate_skips_non_uuid_pk() -> None:
    table = TableInfo(schema="public", name="nodes", rls_enabled=True, rls_forced=False)
    columns = [ColumnInfo("public", "nodes", "id", "int4", False, True)]
    fks = {
        "parent_id": ForeignKeyInfo(
            schema="public",
            table="nodes",
            column="parent_id",
            ref_schema="public",
            ref_table="nodes",
            ref_column="id",
        )
    }
    assert _pregenerate_self_reference(table, columns, fks, ("id",)) == {}


def test_build_seed_plan_orders_root_first_across_schemas() -> None:
    tables = [
        TableInfo(schema="app", name="projects", rls_enabled=True, rls_forced=False),
        TableInfo(schema="core", name="orgs", rls_enabled=True, rls_forced=False),
    ]
    intro = IntrospectionResult(
        tables=tables,
        foreign_keys=[
            ForeignKeyInfo("app", "projects", "org_id", "core", "orgs", "id"),
        ],
        columns=[ColumnInfo("app", "projects", "org_id", "uuid", False, False)],
    )
    cfg = Config(connection=ConnectionConfig(url="postgresql://noop"), tenancy=TenancyConfig(tenant_column="org_id"))
    plan = build_seed_plan(intro, cfg)
    names = [t.qualified for t in plan.ordered_tables]
    assert plan.tenant_root == ("core", "orgs", "id")
    assert names.index("core.orgs") < names.index("app.projects")
