"""Stock trading integration: preview safety and a resting order + cancel."""

import pytest

from ibkr_options.config import load_config
from ibkr_options.conn import resolve_account
from ibkr_options.orders import cancel_order, execute_stock, preview_stock

pytestmark = pytest.mark.integration


def _stock_params(symbol, side, qty, limit):
    return {"kind": "stock", "mode": "paper", "symbol": symbol,
            "side": side, "qty": qty, "limit": limit, "tif": "DAY"}


def test_stock_preview_places_nothing(lab):
    params = _stock_params(lab.market["symbol"], "BUY", 1, 1.00)
    before = lab.open_order_ids()
    out = preview_stock(lab.ib, params)
    lab.ib.sleep(1)
    assert out["token"]
    assert out["notional_usd"] == 1.00
    assert out["reference_close"] and out["reference_close"] > 0
    assert lab.open_order_ids() == before


def test_stock_resting_buy_then_cancel(lab):
    # $1 limit on a ~$300 stock is far below market: rests, never fills.
    acct = resolve_account(lab.ib, load_config("paper"))
    params = _stock_params(lab.market["symbol"], "BUY", 1, 1.00)
    out = preview_stock(lab.ib, params)
    res = execute_stock(lab.ib, acct, out["token"], params)
    lab.ib.sleep(2)
    oid = res["order_id"]
    assert oid in lab.open_order_ids(), "stock order should rest"
    assert res["account"] == acct
    cancel_order(lab.ib, oid)
    lab.ib.sleep(1.5)
    assert oid not in lab.open_order_ids()
