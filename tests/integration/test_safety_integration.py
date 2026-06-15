"""Integration tests for the real-money safety changes: account resolution,
account pinning on live order objects, and rejection-reason surfacing."""

import pytest

from ibkr_options.config import load_config
from ibkr_options.conn import resolve_account
from ibkr_options.orders import cancel_order, execute_single, preview_single

pytestmark = pytest.mark.integration


def test_resolve_account_returns_paper_account(ib):
    acct = resolve_account(ib, load_config("paper"))
    assert acct.startswith("DU"), acct


def test_resting_order_is_pinned_to_account(lab):
    acct = resolve_account(lab.ib, load_config("paper"))
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, 1.00)  # rests
    out = preview_single(lab.ib, params)
    res = execute_single(lab.ib, acct, out["token"], params)
    lab.ib.sleep(2)
    assert res["account"] == acct
    match = [t for t in lab.ib.openTrades() if t.order.orderId == res["order_id"]]
    assert match, "order should be resting"
    assert match[0].order.account == acct
    cancel_order(lab.ib, res["order_id"])
    lab.ib.sleep(1.5)


def test_too_aggressive_order_is_reported_rejected(lab):
    # A limit far above the ask trips IBKR's price-cap (error 202); the result
    # must clearly report the rejection rather than read as a working order.
    _bid, ask = lab.quote(lab.market["itm_call"], "C")
    if ask is None:
        pytest.skip("no ask quote to base an over-aggressive price on")
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, round(ask * 2 + 10, 2))
    out = preview_single(lab.ib, params)
    res = execute_single(lab.ib, None, out["token"], params)
    if res.get("status") in ("PreSubmitted", "Submitted", "Filled"):
        pytest.skip("paper engine accepted the aggressive price; cannot test rejection")
    assert res.get("rejected") is True, res
    assert res.get("messages"), "a rejection must carry a reason"
