"""Order-lifecycle integration tests: preview safety, resting orders, cancel,
token rejection, and a marketable fill. All place real paper orders and clean up."""

import pytest

from ibkr_options import tokens
from ibkr_options.orders import (
    cancel_order,
    execute_single,
    list_open_orders,
    preview_single,
)

pytestmark = pytest.mark.integration


def test_preview_places_nothing_and_emits_token(lab):
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, 1.00)
    before = lab.open_order_ids()
    out = preview_single(lab.ib, params)
    lab.ib.sleep(1)
    assert out["token"]
    assert lab.open_order_ids() == before, "preview must not place an order"


def test_execute_rests_then_cancel_removes_it(lab):
    # ITM call is worth several dollars; a $1 limit cannot fill, so it rests.
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, 1.00)
    out = preview_single(lab.ib, params)
    res = execute_single(lab.ib, out["token"], params)
    lab.ib.sleep(2)

    oid = res["order_id"]
    assert oid in lab.open_order_ids(), "resting order should appear in open orders"

    cancel_order(lab.ib, oid)
    lab.ib.sleep(1.5)
    assert oid not in lab.open_order_ids(), "order should be gone after cancel"


def test_reused_token_is_rejected_and_places_nothing(lab):
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, 1.00)
    out = preview_single(lab.ib, params)
    execute_single(lab.ib, out["token"], params)
    lab.ib.sleep(1)
    before = lab.open_order_ids()
    with pytest.raises(tokens.TokenError):
        execute_single(lab.ib, out["token"], params)
    assert lab.open_order_ids() == before
    # clean up the resting order this test created
    for oid in before:
        try:
            cancel_order(lab.ib, oid)
        except Exception:
            pass


def test_tampered_params_rejected_and_places_nothing(lab):
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, 1.00)
    out = preview_single(lab.ib, params)
    before = lab.open_order_ids()
    tampered = {**params, "qty": 50}
    with pytest.raises(tokens.TokenError):
        execute_single(lab.ib, out["token"], tampered)
    lab.ib.sleep(1)
    assert lab.open_order_ids() == before, "no order should be placed for a tampered token"


def test_marketable_buy_fills_into_a_position(lab):
    opt = lab.resolve(lab.market["itm_call"], "C")
    _bid, ask = lab.quote(lab.market["itm_call"], "C")
    if ask is None:
        pytest.skip("no ask quote available for marketable order")
    # Cross the spread by one tick. A large buffer trips IBKR's
    # too-aggressive-limit guard (error 202) and the order is rejected.
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, round(ask + 0.05, 2))
    out = preview_single(lab.ib, params)
    execute_single(lab.ib, out["token"], params)
    if not lab.wait_position(opt.conId, timeout=10):
        pytest.skip("paper did not fill (market likely closed)")
    assert lab.position_qty(opt.conId) == 1.0
