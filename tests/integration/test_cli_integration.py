"""End-to-end CLI tests via subprocess: exit codes and JSON contract."""

import pytest

from conftest import run_cli

pytestmark = pytest.mark.integration


def test_cli_status_connects(ib):
    code, out = run_cli("status")
    assert code == 0, out
    assert out["mode"] == "paper"
    assert out["connected"] is True
    assert float(out["NetLiquidation"]) > 0


def test_cli_bad_args_exit3(ib):
    code, out = run_cli("close")  # neither query nor --all
    assert code == 3
    assert "error" in out


def test_cli_unknown_symbol_exit3(ib):
    code, out = run_cli("quote", "ZZ9QXZ")
    assert code == 3
    assert "error" in out


def test_cli_preview_emits_token_and_places_nothing(ib, market):
    code, out = run_cli(
        "place", "--symbol", market["symbol"], "--expiry", market["expiry"],
        "--strike", str(market["itm_call"]), "--right", "C",
        "--side", "BUY", "--qty", "1", "--limit", "1.00",
    )
    assert code == 0, out
    assert out["action"] == "place_single"
    assert out["token"]
    # nothing should be resting from a preview
    ocode, orders = run_cli("orders")
    assert ocode == 0
    assert all(o["limit"] != 1.00 for o in orders["orders"])
