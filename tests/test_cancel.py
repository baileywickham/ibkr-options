"""cancel_order: account-wide visibility, but only cancels orders this CLI
placed (nonzero orderId). External orders (client_id 0 / perm_id) are refused
with a clear message rather than silently mishandled."""

import pytest

from ibkr_options import orders


class FakeOrder:
    def __init__(self, orderId, permId, clientId, action="SELL", qty=1, lmt=5.0, tif="GTC"):
        self.orderId = orderId
        self.permId = permId
        self.clientId = clientId
        self.action = action
        self.totalQuantity = qty
        self.lmtPrice = lmt
        self.orderType = "LMT"
        self.tif = tif


class FakeStatus:
    status, filled, remaining, avgFillPrice = "Cancelled", 0.0, 1.0, 0.0


class FakeContract:
    localSymbol = "TLT   261218C00090000"
    symbol = "TLT"
    conId = 999


class FakeTrade:
    def __init__(self, order):
        self.order = order
        self.orderStatus = FakeStatus()
        self.contract = FakeContract()
        self.log = []


class FakeIB:
    def __init__(self, trades):
        self._trades = trades
        self.cancelled = []

    def reqAllOpenOrders(self):
        pass

    def openTrades(self):
        return self._trades

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)

    def sleep(self, s):
        pass


OURS = FakeTrade(FakeOrder(orderId=37, permId=1135600001, clientId=17))
EXTERNAL = FakeTrade(FakeOrder(orderId=0, permId=1135600099, clientId=0))


def test_cancels_own_order_by_orderid():
    ib = FakeIB([OURS, EXTERNAL])
    out = orders.cancel_order(ib, 37)
    assert ib.cancelled == [37]
    assert out["action"] == "cancelled"


def test_successful_cancel_is_not_flagged_rejected():
    # A cancel yields status Cancelled with 0 filled; the placement-rejection
    # heuristic must NOT mislabel that clean cancel as rejected.
    ib = FakeIB([OURS, EXTERNAL])
    out = orders.cancel_order(ib, 37)
    assert out["status"] == "Cancelled"
    assert "rejected" not in out


def test_zero_is_rejected_and_cancels_nothing():
    ib = FakeIB([OURS, EXTERNAL])
    with pytest.raises(ValueError, match="not a valid order id"):
        orders.cancel_order(ib, 0)
    assert ib.cancelled == []


def test_external_order_permid_is_refused_with_guidance():
    ib = FakeIB([OURS, EXTERNAL])
    with pytest.raises(ValueError, match="another channel"):
        orders.cancel_order(ib, 1135600099)
    assert ib.cancelled == []


def test_unknown_id_errors():
    ib = FakeIB([OURS, EXTERNAL])
    with pytest.raises(ValueError, match="no open order"):
        orders.cancel_order(ib, 12345)
    assert ib.cancelled == []
