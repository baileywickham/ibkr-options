"""Order preview and execution. Limit orders only.

Every trading action is two-phase: preview_* returns a dict including a one-shot
token; execute_* consumes the token (tokens.consume raises unless the parameters
are byte-identical to the previewed ones, the preview exists, and it is fresh).
"""

from ib_async import ComboLeg, Contract, LimitOrder

from . import tokens
from .market import get_ticker, num, quote_fields, resolve_option
from .verticals import vertical_risk


def _order_status(ib, trade) -> dict:
    ib.sleep(1.5)
    return {
        "order_id": trade.order.orderId,
        "status": trade.orderStatus.status,
        "filled": num(trade.orderStatus.filled),
        "remaining": num(trade.orderStatus.remaining),
        "avg_fill_price": num(trade.orderStatus.avgFillPrice),
    }


# -- single leg ---------------------------------------------------------------

def preview_single(ib, params: dict) -> dict:
    opt = resolve_option(ib, params["symbol"], params["expiry"], params["strike"], params["right"])
    ticker, data_kind = get_ticker(ib, opt, want_greeks=True)
    quote = quote_fields(ticker)
    cost = None
    if params.get("limit") is not None:
        cost = round(params["limit"] * 100 * params["qty"], 2)
    return {
        "action": "place_single",
        "mode": params["mode"],
        "contract": opt.localSymbol,
        "order": params,
        "quote": quote,
        "data": data_kind,
        "premium_usd": cost,
        "token": tokens.save_pending(params),
    }


def execute_single(ib, token: str, params: dict) -> dict:
    tokens.consume(token, params)
    opt = resolve_option(ib, params["symbol"], params["expiry"], params["strike"], params["right"])
    order = LimitOrder(params["side"], params["qty"], params["limit"], tif=params["tif"])
    trade = ib.placeOrder(opt, order)
    return {"action": "placed_single", "mode": params["mode"], "contract": opt.localSymbol,
            **_order_status(ib, trade)}


# -- vertical spread ----------------------------------------------------------

def _vertical_legs(ib, params: dict):
    long_leg = resolve_option(ib, params["symbol"], params["expiry"], params["long_strike"], params["right"])
    short_leg = resolve_option(ib, params["symbol"], params["expiry"], params["short_strike"], params["right"])
    return long_leg, short_leg


def _combo_contract(symbol: str, long_leg, short_leg) -> Contract:
    return Contract(
        secType="BAG",
        symbol=symbol,
        currency="USD",
        exchange="SMART",
        comboLegs=[
            ComboLeg(conId=long_leg.conId, ratio=1, action="BUY", exchange="SMART"),
            ComboLeg(conId=short_leg.conId, ratio=1, action="SELL", exchange="SMART"),
        ],
    )


def preview_vertical(ib, params: dict) -> dict:
    long_leg, short_leg = _vertical_legs(ib, params)
    long_q, data_kind = get_ticker(ib, long_leg)
    short_q, _ = get_ticker(ib, short_leg)
    width = abs(params["long_strike"] - params["short_strike"])
    risk = vertical_risk(params["side"], params["qty"], params["limit"], width)
    return {
        "action": "place_vertical",
        "mode": params["mode"],
        "legs": {
            f"long ({long_leg.localSymbol})": quote_fields(long_q),
            f"short ({short_leg.localSymbol})": quote_fields(short_q),
        },
        "order": params,
        "width": width,
        **risk,
        "data": data_kind,
        "token": tokens.save_pending(params),
    }


def execute_vertical(ib, token: str, params: dict) -> dict:
    tokens.consume(token, params)
    long_leg, short_leg = _vertical_legs(ib, params)
    combo = _combo_contract(params["symbol"], long_leg, short_leg)
    order = LimitOrder(params["side"], params["qty"], params["limit"], tif=params["tif"])
    trade = ib.placeOrder(combo, order)
    return {"action": "placed_vertical", "mode": params["mode"],
            "legs": [long_leg.localSymbol, short_leg.localSymbol],
            **_order_status(ib, trade)}


# -- cancel -------------------------------------------------------------------

def cancel_order(ib, order_id: int) -> dict:
    ib.reqOpenOrders()
    ib.sleep(0.5)
    for trade in ib.openTrades():
        if trade.order.orderId == order_id:
            ib.cancelOrder(trade.order)
            return {"action": "cancelled", **_order_status(ib, trade)}
    raise ValueError(f"no open order with id {order_id}")


# -- close (flatten) ----------------------------------------------------------

def _norm(s: str) -> str:
    return "".join((s or "").split()).upper()


def _closing_action(qty: float) -> str:
    return "SELL" if qty > 0 else "BUY"


def find_positions(ib, query: str | None) -> list:
    """Open positions, optionally filtered by a whitespace-insensitive substring
    match against the contract's localSymbol/symbol."""
    live = [p for p in ib.positions() if num(p.position)]
    if not query:
        return live
    q = _norm(query)
    return [p for p in live if q in _norm(p.contract.localSymbol or p.contract.symbol)]


def preview_close(ib, mode: str, query: str | None, limit_override: float | None, tif: str) -> dict:
    matches = find_positions(ib, query)
    if not matches:
        raise ValueError(f"no open position matching {query!r}" if query
                         else "no open positions to close")
    plan, display = [], []
    for pos in matches:
        contract = pos.contract
        if not contract.exchange:
            contract.exchange = "SMART"
        qty = num(pos.position)
        action = _closing_action(qty)
        ticker, kind = get_ticker(ib, contract)
        bid, ask = num(ticker.bid), num(ticker.ask)
        if limit_override is not None:
            limit = limit_override
        else:
            limit = bid if action == "SELL" else ask
        if limit is None:
            side = "bid" if action == "SELL" else "ask"
            raise ValueError(
                f"no {side} quote for {contract.localSymbol}; pass --limit to set "
                f"a closing price manually"
            )
        plan.append({"conId": contract.conId, "localSymbol": contract.localSymbol or contract.symbol,
                     "action": action, "qty": abs(qty), "limit": limit, "tif": tif})
        display.append({"contract": contract.localSymbol or contract.symbol, "current_qty": qty,
                        "action": action, "close_qty": abs(qty), "limit": limit,
                        "bid": bid, "ask": ask, "data": kind})
    params = {"kind": "close", "mode": mode, "orders": plan}
    return {"action": "close_preview", "mode": mode, "positions": display,
            "token": tokens.save_pending(params)}


def execute_close(ib, token: str) -> dict:
    params = tokens.consume_stored(token)
    by_conid = {p.contract.conId: p for p in ib.positions() if num(p.position)}
    results = []
    for o in params["orders"]:
        pos = by_conid.get(o["conId"])
        if pos is None:
            # Position already gone since preview — never place, would re-open it.
            results.append({"contract": o["localSymbol"], "status": "skipped_not_open"})
            continue
        contract = pos.contract
        if not contract.exchange:
            contract.exchange = "SMART"
        order = LimitOrder(o["action"], o["qty"], o["limit"], tif=o["tif"])
        trade = ib.placeOrder(contract, order)
        results.append({"contract": o["localSymbol"], "action": o["action"],
                        **_order_status(ib, trade)})
    return {"action": "closed", "mode": params["mode"], "orders": results}


# -- account state ------------------------------------------------------------

def list_positions(ib) -> list[dict]:
    return [
        {
            "account": p.account,
            "contract": p.contract.localSymbol or p.contract.symbol,
            "sec_type": p.contract.secType,
            "qty": num(p.position),
            "avg_cost": num(p.avgCost),
        }
        for p in ib.positions()
    ]


def list_open_orders(ib) -> list[dict]:
    ib.reqOpenOrders()
    ib.sleep(0.5)
    return [
        {
            "order_id": t.order.orderId,
            "contract": t.contract.localSymbol or t.contract.symbol,
            "side": t.order.action,
            "qty": num(t.order.totalQuantity),
            "type": t.order.orderType,
            "limit": num(t.order.lmtPrice),
            "tif": t.order.tif,
            "status": t.orderStatus.status,
        }
        for t in ib.openTrades()
    ]


def list_trades(ib) -> list[dict]:
    return [
        {
            "time": str(f.time),
            "contract": f.contract.localSymbol or f.contract.symbol,
            "side": f.execution.side,
            "qty": num(f.execution.shares),
            "price": num(f.execution.price),
        }
        for f in ib.fills()
    ]


def account_summary(ib) -> dict:
    rows = ib.accountSummary()
    keep = {"NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds", "MaintMarginReq"}
    out = {r.tag: r.value for r in rows if r.tag in keep}
    accounts = sorted({r.account for r in rows})
    return {"accounts": accounts, **out}
