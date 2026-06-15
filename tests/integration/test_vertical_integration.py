"""Vertical-spread integration tests: risk math on a live preview, and a resting
combo order that we then cancel."""

import pytest

from ibkr_options.orders import cancel_order, execute_vertical, preview_vertical

pytestmark = pytest.mark.integration


def test_vertical_preview_risk_matches_formula(lab):
    width = abs(lab.market["v_long"] - lab.market["v_short"])
    debit = 0.50
    out = preview_vertical(lab.ib, lab.vertical_params("BUY", 1, debit))
    assert out["width"] == width
    assert out["max_loss_usd"] == round(debit * 100, 2)
    assert out["max_gain_usd"] == round((width - debit) * 100, 2)
    assert out["token"]


def test_vertical_execute_rests_then_cancel(lab):
    # A tiny debit on an OTM call spread is not marketable, so the combo rests.
    params = lab.vertical_params("BUY", 1, 0.01)
    out = preview_vertical(lab.ib, params)
    before = lab.open_order_ids()
    res = execute_vertical(lab.ib, None, out["token"], params)
    lab.ib.sleep(2)

    oid = res["order_id"]
    if oid not in lab.open_order_ids():
        # combo filled or was rejected by the paper engine; flatten handles it
        pytest.skip("combo did not rest (filled or rejected by paper engine)")
    cancel_order(lab.ib, oid)
    lab.ib.sleep(1.5)
    assert oid not in lab.open_order_ids()
