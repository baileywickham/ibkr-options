"""The invariant that matters most: previews never place orders, and executes
refuse to run without a valid matching token."""

import math

import pytest

from ibkr_options import orders, tokens


class FakeGreeks:
    delta, gamma, theta, vega, impliedVol = 0.45, 0.02, -0.05, 0.11, 0.32


class FakeTicker:
    bid, ask, last = 3.40, 3.60, 3.50
    modelGreeks = FakeGreeks()

    def marketPrice(self):
        return 3.5


class FakeContract:
    def __init__(self, local):
        self.localSymbol = local
        self.conId = hash(local) % 100000


class FakeIB:
    def __init__(self):
        self.placed = []

    def reqMarketDataType(self, kind):
        pass

    def reqTickers(self, contract):
        return [FakeTicker()]

    def qualifyContracts(self, contract):
        resolved = FakeContract(f"{contract.symbol} {contract.lastTradeDateOrContractMonth} "
                                f"{contract.strike}{contract.right}")
        return [resolved]

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        raise AssertionError("placeOrder must never be reached in these tests")


SINGLE = {"kind": "single", "mode": "paper", "symbol": "AAPL", "expiry": "20260717",
          "strike": 200.0, "right": "C", "side": "BUY", "qty": 1, "limit": 3.5, "tif": "DAY"}

VERTICAL = {"kind": "vertical", "mode": "paper", "symbol": "AAPL", "expiry": "20260717",
            "long_strike": 200.0, "short_strike": 205.0, "right": "C", "side": "BUY",
            "qty": 1, "limit": 1.8, "tif": "DAY"}


@pytest.fixture(autouse=True)
def isolated_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "PENDING_DIR", tmp_path)


def test_preview_single_never_places():
    ib = FakeIB()
    out = orders.preview_single(ib, SINGLE)
    assert ib.placed == []
    assert out["token"] == tokens.make_token(SINGLE)
    assert out["premium_usd"] == 350.0
    assert out["quote"]["mid"] == 3.5


def test_preview_vertical_never_places_and_reports_risk():
    ib = FakeIB()
    out = orders.preview_vertical(ib, VERTICAL)
    assert ib.placed == []
    assert out["max_loss_usd"] == 180.0
    assert out["max_gain_usd"] == 320.0


def test_execute_without_preview_is_rejected_before_any_ib_call():
    ib = FakeIB()
    with pytest.raises(tokens.TokenError):
        orders.execute_single(ib, tokens.make_token(SINGLE), SINGLE)
    assert ib.placed == []


def test_execute_with_token_for_different_order_is_rejected():
    ib = FakeIB()
    out = orders.preview_single(ib, SINGLE)
    bigger = {**SINGLE, "qty": 50}
    with pytest.raises(tokens.TokenError):
        orders.execute_single(ib, out["token"], bigger)
    assert ib.placed == []
