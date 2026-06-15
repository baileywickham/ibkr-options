"""Close-command unit tests: correct offsetting orders, marketable pricing,
preview-never-places, and refusal to re-open a position that's already gone."""

import pytest

from ibkr_options import orders, tokens


class FakeContract:
    def __init__(self, conid, local, exchange="SMART"):
        self.conId = conid
        self.localSymbol = local
        self.symbol = local.split()[0]
        self.exchange = exchange
        self.secType = "OPT"


class FakePosition:
    def __init__(self, contract, position):
        self.contract = contract
        self.position = position


class FakeTicker:
    def __init__(self, bid, ask):
        self.bid, self.ask = bid, ask
        self.last = self.close = None
        self.modelGreeks = None


class FakeOrderStatus:
    status, filled, remaining, avgFillPrice = "PreSubmitted", 0.0, 1.0, 0.0


class FakeTrade:
    def __init__(self, order):
        self.order = order
        self.orderStatus = FakeOrderStatus()


class FakeOrder:
    orderId = 99


LONG = FakeContract(101, "AAPL  260717C00355000")
SHORT = FakeContract(102, "AAPL  260717C00360000")


class FakeIB:
    def __init__(self, positions, quotes):
        self._positions = positions
        self._quotes = quotes  # conId -> (bid, ask)
        self.placed = []

    def positions(self):
        return self._positions

    def reqMarketDataType(self, kind):
        pass

    def reqMktData(self, contract, *a):
        return FakeTicker(*self._quotes[contract.conId])

    def cancelMktData(self, contract):
        pass

    def sleep(self, s):
        pass

    @property
    def marketDataType(self):
        return 3

    def placeOrder(self, contract, order):
        self.placed.append((contract.conId, order.action, order.totalQuantity, order.lmtPrice))
        return FakeTrade(order)


@pytest.fixture(autouse=True)
def isolated_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "PENDING_DIR", tmp_path)


def make_ib():
    return FakeIB(
        positions=[FakePosition(LONG, 1.0), FakePosition(SHORT, -1.0)],
        quotes={101: (0.08, 0.10), 102: (0.05, 0.10)},
    )


def test_preview_builds_offsetting_marketable_orders_and_places_nothing():
    ib = make_ib()
    out = orders.preview_close(ib, "paper", None, None, "DAY")
    assert ib.placed == []
    plan = {p["contract"]: p for p in out["positions"]}
    # long -> SELL at bid; short -> BUY at ask
    assert plan["AAPL  260717C00355000"]["action"] == "SELL"
    assert plan["AAPL  260717C00355000"]["limit"] == 0.08
    assert plan["AAPL  260717C00360000"]["action"] == "BUY"
    assert plan["AAPL  260717C00360000"]["limit"] == 0.10


def test_query_filters_to_one_position():
    ib = make_ib()
    out = orders.preview_close(ib, "paper", "355", None, "DAY")
    assert [p["contract"] for p in out["positions"]] == ["AAPL  260717C00355000"]


def test_limit_override_applied():
    ib = make_ib()
    out = orders.preview_close(ib, "paper", "355", 0.03, "DAY")
    assert out["positions"][0]["limit"] == 0.03


def test_missing_quote_requires_manual_limit():
    ib = FakeIB([FakePosition(LONG, 1.0)], {101: (None, None)})
    with pytest.raises(ValueError, match="no bid quote"):
        orders.preview_close(ib, "paper", None, None, "DAY")
    # but an override lets it through
    out = orders.preview_close(ib, "paper", None, 0.01, "DAY")
    assert out["positions"][0]["limit"] == 0.01


def test_no_matching_position_errors():
    ib = make_ib()
    with pytest.raises(ValueError, match="no open position matching"):
        orders.preview_close(ib, "paper", "TSLA", None, "DAY")


def test_execute_places_planned_offsetting_orders():
    ib = make_ib()
    token = orders.preview_close(ib, "paper", None, None, "DAY")["token"]
    out = orders.execute_close(ib, token)
    assert len(ib.placed) == 2
    assert (101, "SELL", 1.0, 0.08) in ib.placed
    assert (102, "BUY", 1.0, 0.10) in ib.placed
    assert all(o.get("status") != "skipped_not_open" for o in out["orders"])


def test_execute_skips_position_that_vanished():
    ib = make_ib()
    token = orders.preview_close(ib, "paper", None, None, "DAY")["token"]
    # position 102 closed out elsewhere between preview and execute
    ib._positions = [FakePosition(LONG, 1.0)]
    out = orders.execute_close(ib, token)
    assert [p[0] for p in ib.placed] == [101]
    statuses = {o["contract"]: o.get("status") for o in out["orders"]}
    assert statuses["AAPL  260717C00360000"] == "skipped_not_open"


def test_close_token_is_one_shot():
    ib = make_ib()
    token = orders.preview_close(ib, "paper", None, None, "DAY")["token"]
    orders.execute_close(ib, token)
    with pytest.raises(tokens.TokenError):
        orders.execute_close(ib, token)
