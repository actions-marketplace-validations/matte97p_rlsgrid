"""CLI-level tests that do not need a reachable database."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rlsgrid.cli import main

UNREACHABLE = (
    "postgresql://postgres:postgres@127.0.0.1:1/rlsgrid_nope?connect_timeout=1"
)


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "rlsgrid.toml"
    cfg.write_text(
        f'[connection]\nurl = "{UNREACHABLE}"\n\n'
        '[roles]\nauthenticated = "user"\n'
    )
    return cfg


def test_init_writes_config(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "rlsgrid.toml"
    result = runner.invoke(main, ["init", "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "[connection]" in out.read_text()


def test_missing_config_exits_2(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["introspect", "--config", str(tmp_path / "nope.toml")])
    assert result.exit_code == 2
    assert "Config not found" in result.output


def test_unreachable_db_exits_4_with_clean_message(tmp_path: Path) -> None:
    runner = CliRunner()
    cfg = _write_config(tmp_path)
    result = runner.invoke(main, ["introspect", "--config", str(cfg)])
    assert result.exit_code == 4
    assert "Cannot connect to the database" in result.output
    # no raw psycopg traceback leaked to the user
    assert "Traceback" not in result.output


def test_seed_unreachable_db_exits_4(tmp_path: Path) -> None:
    runner = CliRunner()
    cfg = _write_config(tmp_path)
    result = runner.invoke(main, ["seed", "--config", str(cfg), "--tenants", "2"])
    assert result.exit_code == 4
    assert "Cannot connect to the database" in result.output
