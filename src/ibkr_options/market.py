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


def _has_price(ticker) -> bool:
    return any(
        num(v) is not None
        for v in (ticker.bid, ticker.ask, ticker.last, ticker.close)
    )


def data_kind(ib) -> str:
    return "realtime" if getattr(ib, "marketDataType", 3) == 1 else "delayed"


def get_ticker(ib, contract, wait: float = 4.0, want_greeks: bool = False):
    """Stream a ticker and wait briefly for prices to arrive.

    The market-data type is set once at connect time (see conn.connect); we do
    not toggle it here, which is what triggers IBKR pacing errors. Streaming +
    a short wait is far more reliable than snapshots under delayed data.
    """
    ticker = ib.reqMktData(contract, "", False, False)
    deadline, step = wait, 0.25
    while deadline > 0:
        ib.sleep(step)
        deadline -= step
        ready = _has_price(ticker) and (not want_greeks or ticker.modelGreeks is not None)
        if ready:
            break
    ib.cancelMktData(contract)
    return ticker, data_kind(ib)


def spot_price(ib, stock) -> float | None:
    """Reference price for the underlying via the last daily bar.

    Streaming stock quotes intermittently fail under delayed data (IBKR error
    322 on some NASDAQ names); the historical-data path is reliable.
    """
    bars = ib.reqHistoricalData(
        stock, endDateTime="", durationStr="1 D", barSizeSetting="1 day",
        whatToShow="TRADES", useRTH=True, formatDate=1,
    )
    return num(bars[-1].close) if bars else None


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
    if not [c for c in ib.qualifyContracts(stk) if c is not None]:
        raise ContractNotFound(f"no US stock found for symbol {symbol!r}")
    return stk


def resolve_option(ib, symbol: str, expiry: str, strike: float, right: str) -> Option:
    opt = Option(symbol, expiry, strike, right, "SMART", currency="USD")
    # ib_async returns [None] (not []) for a contract it cannot qualify.
    qualified = [c for c in ib.qualifyContracts(opt) if c is not None]
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
    spot = spot_price(ib, stk)
    kind = data_kind(ib)

    chains = ib.reqSecDefOptParams(stk.symbol, "", stk.secType, stk.conId)
    smart = [c for c in chains if c.exchange == "SMART"]
    if not smart:
        raise ContractNotFound(f"no SMART option chain for {symbol}")
    chain = smart[0]

    expirations = sorted(chain.expirations)
    if expiry is None:
        return {"symbol": symbol, "spot": spot, "data": kind, "expirations": expirations}

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
            ticker, _ = get_ticker(ib, opt, want_greeks=True)
            row["call" if right == "C" else "put"] = quote_fields(ticker)
        if "call" in row or "put" in row:
            rows.append(row)
    return {
        "symbol": symbol,
        "spot": spot,
        "expiry": expiry,
        "data": kind,
        "strikes": rows,
    }
