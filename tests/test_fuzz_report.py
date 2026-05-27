"""Unit tests for FuzzReport skip accounting — no DB required."""

from __future__ import annotations

from rlsgrid.fuzz.chaos import FuzzReport, _Skip


def test_record_skip_counts_by_reason() -> None:
    report = FuzzReport(iterations=10)
    report._record_skip(_Skip("no target row on this table"))
    report._record_skip(_Skip("no target row on this table"))
    report._record_skip(_Skip("table has no primary key"))
    assert report.skipped == 3
    assert report.skip_reasons["no target row on this table"] == 2
    assert report.skip_reasons["table has no primary key"] == 1


def test_ok_property_tracks_breaches() -> None:
    report = FuzzReport(iterations=1)
    assert report.ok is True
    report.breaches.append(object())  # type: ignore[arg-type]
    assert report.ok is False
