"""Stock order preview/execute unit tests."""

import pytest

from ibkr_options import orders, tokens


class Bar:
    close = 150.0


class FakeStatus:
    status, filled, remaining, avgFillPrice = "PreSubmitted", 0.0, 100.0, 0.0


class FakeTrade:
    def __init__(self, order):
        self.order = order
        self.orderStatus = FakeStatus()
        self.log = []


class FakeIB:
    def __init__(self):
        self.placed = []

    def sleep(self, s):
        pass

    def qualifyContracts(self, contract):
        contract.conId = 265598
        return [contract]

    def reqHistoricalData(self, *a, **k):
        return [Bar()]

    def placeOrder(self, contract, order):
        self.placed.append((contract.secType, order.action, order.totalQuantity,
                            order.lmtPrice, order.account))
        return FakeTrade(order)


PARAMS = {"kind": "stock", "mode": "live", "symbol": "AAPL",
          "side": "BUY", "qty": 100, "limit": 149.50, "tif": "DAY"}


@pytest.fixture(autouse=True)
def isolated_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "PENDING_DIR", tmp_path)


def test_preview_stock_computes_notional_and_places_nothing():
    ib = FakeIB()
    out = orders.preview_stock(ib, PARAMS)
    assert ib.placed == []
    assert out["notional_usd"] == round(149.50 * 100, 2)
    assert out["reference_close"] == 150.0
    assert out["token"] == tokens.make_token(PARAMS)


def test_execute_stock_places_account_pinned_order():
    ib = FakeIB()
    token = orders.preview_stock(ib, PARAMS)["token"]
    out = orders.execute_stock(ib, "U777", token, PARAMS)
    assert ib.placed == [("STK", "BUY", 100, 149.50, "U777")]
    assert out["account"] == "U777"
    assert out["action"] == "placed_stock"


def test_execute_stock_rejects_tampered_params():
    ib = FakeIB()
    token = orders.preview_stock(ib, PARAMS)["token"]
    with pytest.raises(tokens.TokenError):
        orders.execute_stock(ib, "U777", token, {**PARAMS, "qty": 1000})
    assert ib.placed == []
