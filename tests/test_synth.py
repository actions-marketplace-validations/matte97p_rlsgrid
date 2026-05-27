"""Unit tests for synthetic value generation."""

from __future__ import annotations

import uuid

from rlsgrid.synth import synth_value


def test_enum_labels_take_precedence() -> None:
    assert synth_value("anything", enum_labels=["open", "closed"]) == "open"


def test_uuid_is_parseable() -> None:
    uuid.UUID(str(synth_value("uuid")))


def test_numeric_types_are_zero() -> None:
    for t in ("int2", "int4", "int8", "numeric", "float4", "float8"):
        assert synth_value(t) == 0


def test_bool_false() -> None:
    assert synth_value("bool") is False


def test_json_is_object_literal() -> None:
    assert synth_value("jsonb") == "{}"


def test_temporal_is_none_for_db_default() -> None:
    assert synth_value("timestamptz") is None
    assert synth_value("date") is None


def test_bytea_is_empty_bytes() -> None:
    assert synth_value("bytea") == b""


def test_inet_is_cidr_literal() -> None:
    assert synth_value("inet") == "0.0.0.0/0"


def test_unknown_text_fallback() -> None:
    assert synth_value("citext") == "rlsgrid-fixture"
