"""Read-only market-data integration tests (no orders placed)."""

import re

import pytest

from ibkr_options.market import (
    ContractNotFound,
    get_ticker,
    num,
    option_chain,
    resolve_option,
    resolve_stock,
    spot_price,
)
from ibkr_options.orders import account_summary, list_open_orders, list_positions, list_trades

pytestmark = pytest.mark.integration


def test_status_is_paper_account(ib):
    summary = account_summary(ib)
    assert "NetLiquidation" in summary
    assert float(summary["NetLiquidation"]) > 0
    assert any(a.startswith("DU") for a in summary["accounts"]), summary["accounts"]


def test_positions_orders_trades_are_well_formed(ib):
    assert isinstance(list_positions(ib), list)
    assert isinstance(list_open_orders(ib), list)
    assert isinstance(list_trades(ib), list)


def test_chain_expirations_sorted_and_dated(ib, market):
    chain = option_chain(ib, market["symbol"], None, 0)
    exps = chain["expirations"]
    assert exps == sorted(exps)
    assert all(re.fullmatch(r"\d{8}", e) for e in exps)
    assert chain["spot"] and chain["spot"] > 0
    assert chain["data"] in ("delayed", "realtime")


def test_chain_strikes_have_quotes_and_greeks(ib, market):
    chain = option_chain(ib, market["symbol"], market["expiry"], 3)
    assert chain["strikes"], "expected at least one strike row"
    priced = [
        leg
        for row in chain["strikes"]
        for leg in (row.get("call"), row.get("put"))
        if leg
    ]
    assert priced, "expected at least one priced option"
    # at least one leg should carry a bid/ask and greeks under delayed data
    assert any(leg.get("bid") is not None or leg.get("ask") is not None for leg in priced)
    assert any("greeks" in leg for leg in priced)


def test_chain_strikes_centered_near_spot(ib, market):
    chain = option_chain(ib, market["symbol"], market["expiry"], 4)
    strikes = [r["strike"] for r in chain["strikes"]]
    spot = chain["spot"]
    # every returned strike should be within a sane band of spot
    assert all(abs(s - spot) < spot * 0.5 for s in strikes), (strikes, spot)


def test_spot_price_reasonable(ib, market):
    stk = resolve_stock(ib, market["symbol"])
    spot = spot_price(ib, stk)
    assert 10 < spot < 10000


def test_option_quote_has_bid_ask_or_close(ib, market):
    opt = resolve_option(ib, market["symbol"], market["expiry"], market["itm_call"], "C")
    ticker, kind = get_ticker(ib, opt, want_greeks=True)
    assert num(ticker.bid) is not None or num(ticker.ask) is not None or num(ticker.close) is not None
    assert kind in ("delayed", "realtime")


def test_resolve_unknown_symbol_raises(ib):
    with pytest.raises(ContractNotFound):
        resolve_stock(ib, "ZZ9QXZ")


def test_resolve_invalid_strike_raises(ib, market):
    # a clearly non-listed fractional strike
    with pytest.raises(ContractNotFound):
        resolve_option(ib, market["symbol"], market["expiry"], 12345.67, "C")


def test_chain_invalid_expiry_raises(ib, market):
    with pytest.raises(ContractNotFound):
        option_chain(ib, market["symbol"], "20990101", 3)
