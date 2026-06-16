"""Close-command unit tests: correct offsetting orders, marketable pricing,
preview-never-places, and refusal to re-open a position that's already gone."""

import pytest

from ibkr_options import orders, tokens


class FakeContract:
    def __init__(self, conid, local, exchange="SMART", secType="OPT", strike=None, right=""):
        self.conId = conid
        self.localSymbol = local
        self.symbol = local.split()[0]
        self.exchange = exchange
        self.secType = secType
        self.strike = strike
        self.right = right


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
        self.log = []


class FakeOrder:
    orderId = 99


LONG = FakeContract(101, "AAPL  260717C00355000", strike=355.0, right="C")
SHORT = FakeContract(102, "AAPL  260717C00360000", strike=360.0, right="C")


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


def test_preview_closes_spread_as_one_combo_at_net_and_places_nothing():
    ib = make_ib()
    out = orders.preview_close(ib, "paper", None, None, "DAY")
    assert ib.placed == []
    # A long/short option pair is one spread -> a single combo close, not two
    # per-leg orders. Marketable net = long bid - short ask = 0.08 - 0.10.
    assert len(out["positions"]) == 1
    leg = out["positions"][0]
    assert leg["close"] == "spread (combo)"
    assert leg["action"] == "SELL"
    assert leg["net_limit"] == -0.02
    assert leg["legs"] == ["AAPL  260717C00355000", "AAPL  260717C00360000"]


def test_query_filters_to_one_position():
    ib = make_ib()
    out = orders.preview_close(ib, "paper", "355", None, "DAY")
    assert [p["contract"] for p in out["positions"]] == ["AAPL  260717C00355000"]


def test_query_matches_by_strike_and_right():
    ib = make_ib()  # long 355C, short 360C
    # bare strike still works
    assert [c.localSymbol for c in [p.contract for p in orders.find_positions(ib, "355")]] == \
        ["AAPL  260717C00355000"]
    # the documented strike+right form (`close 355C`) now matches
    assert [p.contract.localSymbol for p in orders.find_positions(ib, "355C")] == \
        ["AAPL  260717C00355000"]
    # wrong right matches nothing (355P when only 355C is held)
    assert orders.find_positions(ib, "355P") == []


def test_close_forces_smart_routing():
    # A stock position's native exchange (NASDAQ) must be overridden to SMART so
    # the close isn't direct-routed (which precautionary settings can block).
    stk = FakeContract(201, "AAPL", exchange="NASDAQ", secType="STK")
    ib = FakeIB(positions=[FakePosition(stk, 10.0)], quotes={201: (190.0, 190.1)})
    out = orders.preview_close(ib, "paper", None, None, "DAY")
    assert stk.exchange == "SMART"
    orders.execute_close(ib, None, out["token"])
    assert stk.exchange == "SMART"
    assert ib.placed and ib.placed[0][1] == "SELL"  # SELL 10 shares to close the long


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


def test_execute_places_one_combo_order_for_a_spread():
    ib = make_ib()
    token = orders.preview_close(ib, "paper", None, None, "DAY")["token"]
    out = orders.execute_close(ib, None, token)
    # ONE combo order (BAG conId defaults to 0), not two per-leg orders.
    assert len(ib.placed) == 1
    conid, action, qty, limit = ib.placed[0]
    assert (action, qty, limit) == ("SELL", 1.0, -0.02)
    assert all(o.get("status") != "skipped_not_open" for o in out["orders"])


def test_combo_close_uses_net_limit_not_per_leg():
    """Regression: a --limit on a spread must be the NET combo price on a single
    order, never the same number on each leg (which made the buy-to-close leg
    marketable and legged into a naked option)."""
    ib = make_ib()
    out = orders.preview_close(ib, "paper", None, 8.00, "GTC")
    assert out["positions"][0]["net_limit"] == 8.00
    token = out["token"]
    orders.execute_close(ib, None, token)
    # Exactly one SELL combo at the net price — and crucially NO marketable
    # BUY on the short leg at 8.00.
    assert ib.placed == [(0, "SELL", 1.0, 8.00)]


def test_multileg_with_limit_when_not_a_spread_is_refused():
    """Two same-side legs can't take one per-leg --limit safely -> refuse."""
    ib = FakeIB(
        positions=[FakePosition(LONG, 1.0), FakePosition(SHORT, 1.0)],  # both long
        quotes={101: (0.08, 0.10), 102: (0.05, 0.10)},
    )
    with pytest.raises(ValueError, match="leg you in"):
        orders.preview_close(ib, "paper", None, 8.00, "GTC")
    assert ib.placed == []


def test_execute_skips_spread_if_a_leg_vanished():
    ib = make_ib()
    token = orders.preview_close(ib, "paper", None, None, "DAY")["token"]
    # one leg closed out elsewhere between preview and execute
    ib._positions = [FakePosition(LONG, 1.0)]
    out = orders.execute_close(ib, None, token)
    assert ib.placed == []  # never place a half-combo that could re-open
    assert out["orders"][0]["status"] == "skipped_not_open"


def test_close_token_is_one_shot():
    ib = make_ib()
    token = orders.preview_close(ib, "paper", None, None, "DAY")["token"]
    orders.execute_close(ib, None, token)
    with pytest.raises(tokens.TokenError):
        orders.execute_close(ib, None, token)
