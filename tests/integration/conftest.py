"""Integration test harness against a running IB Gateway (paper, port 4002).

All tests here are marked `integration` and skip automatically when Gateway is
unreachable, so the unit suite still runs without it. The `ib` connection uses a
dedicated clientId so it never collides with the CLI (clientId 17).

Discovery is dynamic (a live expiry and real strikes) so the suite does not rot
as expiries roll off. The `lab` fixture flattens the account before and after
each order/close test.
"""

import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from ib_async import LimitOrder

from ibkr_options.config import load_config
from ibkr_options.conn import GatewayUnreachable, connect
from ibkr_options.market import (
    ContractNotFound,
    get_ticker,
    num,
    resolve_option,
    resolve_stock,
    spot_price,
)
from ibkr_options.orders import cancel_order, list_open_orders, list_positions

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYMBOL = "AAPL"


# --------------------------------------------------------------------------- #
# CLI subprocess helper
# --------------------------------------------------------------------------- #

def run_cli(*args):
    """Run `ibkr <args>` as a real subprocess; return (exit_code, parsed_json|raw)."""
    import json

    proc = subprocess.run(
        ["uv", "run", "ibkr", *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    try:
        return proc.returncode, json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return proc.returncode, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def ib():
    cfg = load_config("paper")
    cfg["client_id"] = 50  # dedicated; CLI uses 17
    try:
        conn = connect(cfg)
    except GatewayUnreachable as exc:
        pytest.skip(f"IB Gateway (paper) not reachable: {exc}")
    yield conn
    try:
        conn.disconnect()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Dynamic market discovery (session-scoped, cheap: no per-strike quotes)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def market(ib):
    stk = resolve_stock(ib, SYMBOL)
    spot = spot_price(ib, stk)
    assert spot and spot > 0, "could not determine spot price"

    params = ib.reqSecDefOptParams(stk.symbol, "", stk.secType, stk.conId)
    chain = next(c for c in params if c.exchange == "SMART")
    expirations = sorted(chain.expirations)
    today = date.today()
    expiry = next(
        (e for e in expirations if 20 <= (datetime.strptime(e, "%Y%m%d").date() - today).days <= 90),
        expirations[min(len(expirations) - 1, 6)],
    )

    fives = sorted(s for s in chain.strikes if s % 5 == 0)

    def first_valid(candidates, right):
        for s in candidates:
            try:
                resolve_option(ib, SYMBOL, expiry, s, right)
                return s
            except ContractNotFound:
                continue
        raise AssertionError(f"no valid {right} strike among {candidates[:5]}...")

    itm_call = first_valid([s for s in reversed(fives) if s <= spot - 5], "C")
    otm_calls = [s for s in fives if s >= spot + 10]
    otm_call = first_valid(otm_calls, "C")
    # two adjacent OTM strikes for a vertical
    v_long = first_valid(otm_calls, "C")
    v_short = first_valid([s for s in otm_calls if s > v_long], "C")

    return {
        "symbol": SYMBOL, "spot": spot, "expiry": expiry,
        "itm_call": itm_call, "otm_call": otm_call,
        "v_long": v_long, "v_short": v_short,
    }


# --------------------------------------------------------------------------- #
# Lab: per-test helpers + automatic flatten before/after
# --------------------------------------------------------------------------- #

class Lab:
    def __init__(self, ib, market):
        self.ib = ib
        self.market = market

    # -- construction --
    def single_params(self, strike, side, qty, limit, right="C", tif="DAY"):
        return {
            "kind": "single", "mode": "paper", "symbol": self.market["symbol"],
            "expiry": self.market["expiry"], "strike": float(strike), "right": right,
            "side": side, "qty": qty, "limit": limit, "tif": tif,
        }

    def vertical_params(self, side, qty, limit, right="C", tif="DAY"):
        return {
            "kind": "vertical", "mode": "paper", "symbol": self.market["symbol"],
            "expiry": self.market["expiry"], "long_strike": float(self.market["v_long"]),
            "short_strike": float(self.market["v_short"]), "right": right,
            "side": side, "qty": qty, "limit": limit, "tif": tif,
        }

    def resolve(self, strike, right="C"):
        return resolve_option(self.ib, self.market["symbol"], self.market["expiry"], strike, right)

    def quote(self, strike, right="C"):
        ticker, _ = get_ticker(self.ib, self.resolve(strike, right))
        return num(ticker.bid), num(ticker.ask)

    # -- account state --
    def position_qty(self, conid):
        for p in self.ib.positions():
            if p.contract.conId == conid and num(p.position):
                return num(p.position)
        return 0

    def open_order_ids(self):
        return {o["order_id"] for o in list_open_orders(self.ib)}

    def wait_position(self, conid, timeout=10):
        while timeout > 0:
            self.ib.sleep(1)
            timeout -= 1
            if self.position_qty(conid):
                return True
        return False

    # -- cleanup --
    def flatten(self):
        self.ib.reqGlobalCancel()
        self.ib.sleep(1.0)
        for pos in list(self.ib.positions()):
            qty = num(pos.position)
            if not qty:
                continue
            contract = pos.contract
            if not contract.exchange:
                contract.exchange = "SMART"
            action = "SELL" if qty > 0 else "BUY"
            ticker, _ = get_ticker(self.ib, contract)
            bid, ask = num(ticker.bid), num(ticker.ask)
            price = (bid if action == "SELL" else ask)
            if price is None:
                price = 0.01 if action == "SELL" else 1000.0
            self.ib.placeOrder(contract, LimitOrder(action, abs(qty), price))
        self.ib.sleep(2.0)


@pytest.fixture
def lab(ib, market):
    box = Lab(ib, market)
    box.flatten()
    yield box
    box.flatten()
