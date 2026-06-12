"""Market data and contract resolution."""

import math

from ib_async import Option, Stock


class ContractNotFound(Exception):
    pass


def num(x):
    """nan/None -> None, else round floats for JSON output."""
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x):
            return None
        return round(x, 4)
    return x


def get_ticker(ib, contract):
    """Fetch a ticker, falling back to delayed data when realtime is unsubscribed.

    Returns (ticker, data_kind) where data_kind is 'realtime' or 'delayed'.
    """
    ib.reqMarketDataType(1)
    [ticker] = ib.reqTickers(contract)
    if num(ticker.bid) is None and num(ticker.ask) is None and num(ticker.last) is None:
        ib.reqMarketDataType(3)
        [ticker] = ib.reqTickers(contract)
        return ticker, "delayed"
    return ticker, "realtime"


def quote_fields(ticker) -> dict:
    bid, ask = num(ticker.bid), num(ticker.ask)
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    out = {"bid": bid, "ask": ask, "mid": mid, "last": num(ticker.last)}
    greeks = ticker.modelGreeks
    if greeks is not None:
        out["greeks"] = {
            "delta": num(greeks.delta),
            "gamma": num(greeks.gamma),
            "theta": num(greeks.theta),
            "vega": num(greeks.vega),
            "iv": num(greeks.impliedVol),
        }
    return out


def resolve_stock(ib, symbol: str) -> Stock:
    stk = Stock(symbol, "SMART", "USD")
    if not ib.qualifyContracts(stk):
        raise ContractNotFound(f"no US stock found for symbol {symbol!r}")
    return stk


def resolve_option(ib, symbol: str, expiry: str, strike: float, right: str) -> Option:
    opt = Option(symbol, expiry, strike, right, "SMART", currency="USD")
    qualified = ib.qualifyContracts(opt)
    if not qualified:
        raise ContractNotFound(
            f"no option contract for {symbol} {expiry} {strike}{right}; "
            f"check the chain for valid expiries/strikes"
        )
    if len(qualified) > 1:
        cands = [f"{c.localSymbol} ({c.tradingClass})" for c in qualified]
        raise ContractNotFound(f"ambiguous option contract, candidates: {cands}")
    return qualified[0]


def option_chain(ib, symbol: str, expiry: str | None, strikes_n: int) -> dict:
    stk = resolve_stock(ib, symbol)
    stock_ticker, data_kind = get_ticker(ib, stk)
    spot = num(stock_ticker.marketPrice())

    chains = ib.reqSecDefOptParams(stk.symbol, "", stk.secType, stk.conId)
    smart = [c for c in chains if c.exchange == "SMART"]
    if not smart:
        raise ContractNotFound(f"no SMART option chain for {symbol}")
    chain = smart[0]

    expirations = sorted(chain.expirations)
    if expiry is None:
        return {"symbol": symbol, "spot": spot, "data": data_kind, "expirations": expirations}

    if expiry not in expirations:
        raise ContractNotFound(f"expiry {expiry} not in chain; available: {expirations}")

    if spot is None:
        raise ContractNotFound(f"no price for {symbol}; cannot pick strikes around spot")
    strikes = sorted(chain.strikes, key=lambda s: abs(s - spot))[:strikes_n]

    rows = []
    for strike in sorted(strikes):
        row = {"strike": strike}
        for right in ("C", "P"):
            try:
                opt = resolve_option(ib, symbol, expiry, strike, right)
            except ContractNotFound:
                continue
            ticker, _ = get_ticker(ib, opt)
            row["call" if right == "C" else "put"] = quote_fields(ticker)
        rows.append(row)
    return {
        "symbol": symbol,
        "spot": spot,
        "expiry": expiry,
        "data": data_kind,
        "strikes": rows,
    }
