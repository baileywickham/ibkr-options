"""Order preview and execution. Limit orders only.

Every trading action is two-phase: preview_* returns a dict including a one-shot
token; execute_* consumes the token (tokens.consume raises unless the parameters
are byte-identical to the previewed ones, the preview exists, and it is fresh).
"""

from ib_async import ComboLeg, Contract, LimitOrder

from . import tokens
from .market import get_ticker, num, quote_fields, resolve_option, resolve_stock, spot_price
from .verticals import vertical_risk

# Order statuses that mean the order is not (and will not become) working.
_DEAD_STATUSES = {"Cancelled", "ApiCancelled", "Inactive"}
# Statuses that mean IBKR has acknowledged a live/working (or filled) order.
_WORKING_STATUSES = {"Filled", "Submitted", "PreSubmitted"}


def _limit_order(account: str | None, side: str, qty, limit, tif: str) -> LimitOrder:
    order = LimitOrder(side, qty, limit, tif=tif)
    if account:
        order.account = account
    return order


def _status_dict(trade, collected: dict | None = None) -> dict:
    """Build the result dict from a trade plus any captured error events.

    Pure (no IB calls) so the status/rejection logic is unit-testable. Messages
    merge two sources keyed by error code: `trade.log` and `collected` (events
    seen via ib.errorEvent). IBKR delivers the meaningful rejection reason — e.g.
    202 'limit too far outside NBBO' — only through the event stream, never in
    trade.log, so both sources are required.
    """
    messages: dict[int, str] = {}
    for entry in trade.log:
        if getattr(entry, "errorCode", 0):
            messages[entry.errorCode] = entry.message
    for code, msg in (collected or {}).items():
        messages[code] = msg

    status = trade.orderStatus.status
    out = {
        "order_id": trade.order.orderId,
        "status": status,
        "filled": num(trade.orderStatus.filled),
        "remaining": num(trade.orderStatus.remaining),
        "avg_fill_price": num(trade.orderStatus.avgFillPrice),
    }
    if messages:
        out["messages"] = [f"[{code}] {msg}" for code, msg in sorted(messages.items())]
    # A dead status with nothing filled is a rejection the caller must see —
    # never let it read as a quietly-working order.
    if status in _DEAD_STATUSES:
        if not num(trade.orderStatus.filled):
            out["rejected"] = True
    elif status not in _WORKING_STATUSES:
        # e.g. still PendingSubmit/ApiPending or blank: IBKR never acknowledged
        # it within the settle window. Not confirmed working — say so explicitly.
        out["unconfirmed"] = True
    return out


def _place(ib, contract, order, settle: float = 4.0) -> dict:
    """Place an order and report its outcome, capturing rejection reasons.

    Subscribes to ib.errorEvent for this order's id so the real reason is caught
    (it never lands in trade.log). Polls rather than reading once, because the
    status can flip to Cancelled a beat before the reason arrives.
    """
    collected: dict[int, str] = {}

    def on_error(reqId, errorCode, errorString, *_a):
        if reqId == order.orderId:
            collected[errorCode] = errorString

    event = getattr(ib, "errorEvent", None)
    if event is not None:
        event += on_error
    try:
        trade = ib.placeOrder(contract, order)
        waited, step = 0.0, 0.5
        while waited < settle:
            ib.sleep(step)
            waited += step
            status = trade.orderStatus.status
            if status == "Filled":
                break
            # A resting order won't change; stop once it's clearly working.
            if status in ("Submitted", "PreSubmitted") and waited >= 2.0:
                break
            # If it's dead, keep polling to settle so the late reason is caught.
    finally:
        if event is not None:
            event -= on_error
    return _status_dict(trade, collected)


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


def execute_single(ib, account: str | None, token: str, params: dict) -> dict:
    # Resolve before consuming the token: a transient resolve failure must not
    # burn the one-shot token and force a re-preview.
    opt = resolve_option(ib, params["symbol"], params["expiry"], params["strike"], params["right"])
    tokens.consume(token, params)
    order = _limit_order(account, params["side"], params["qty"], params["limit"], params["tif"])
    return {"action": "placed_single", "mode": params["mode"], "account": account,
            "contract": opt.localSymbol, **_place(ib, opt, order)}


# -- stock --------------------------------------------------------------------

def preview_stock(ib, params: dict) -> dict:
    stk = resolve_stock(ib, params["symbol"])
    reference = spot_price(ib, stk)  # stock streaming is unreliable; use last daily close
    return {
        "action": "place_stock",
        "mode": params["mode"],
        "contract": stk.symbol,
        "order": params,
        "reference_close": reference,
        "notional_usd": round(params["limit"] * params["qty"], 2),
        "token": tokens.save_pending(params),
    }


def execute_stock(ib, account: str | None, token: str, params: dict) -> dict:
    stk = resolve_stock(ib, params["symbol"])
    tokens.consume(token, params)
    order = _limit_order(account, params["side"], params["qty"], params["limit"], params["tif"])
    return {"action": "placed_stock", "mode": params["mode"], "account": account,
            "contract": stk.symbol, **_place(ib, stk, order)}


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


def execute_vertical(ib, account: str | None, token: str, params: dict) -> dict:
    long_leg, short_leg = _vertical_legs(ib, params)
    combo = _combo_contract(params["symbol"], long_leg, short_leg)
    tokens.consume(token, params)
    order = _limit_order(account, params["side"], params["qty"], params["limit"], params["tif"])
    return {"action": "placed_vertical", "mode": params["mode"], "account": account,
            "legs": [long_leg.localSymbol, short_leg.localSymbol],
            **_place(ib, combo, order)}


# -- cancel -------------------------------------------------------------------

# Consistency rule across the CLI: reads are account-wide (see everything the
# account holds, regardless of which channel placed it); writes act only on
# orders THIS CLI placed. An order's client_id == 0 (or any id that isn't this
# session's) means it came from the web Portal / TWS / mobile and is read-only
# here — manage it where it was placed.

def _all_open_trades(ib) -> list:
    """Every open order for the account, account-wide.

    reqOpenOrders() returns only this client's orders; reqAllOpenOrders() is
    account-wide, so the CLI is never blind to externally-placed resting orders
    when it lists, closes, or cancels.
    """
    ib.reqAllOpenOrders()
    ib.sleep(1.5)
    return ib.openTrades()


def cancel_order(ib, order_id: int) -> dict:
    if order_id == 0:
        raise ValueError("0 is not a valid order id")
    trades = _all_open_trades(ib)
    # Orders this CLI placed are returned with their real (nonzero) orderId;
    # externally-placed orders surface with orderId 0, so this matches only ours.
    for trade in trades:
        if trade.order.orderId == order_id:
            ib.cancelOrder(trade.order)
            ib.sleep(1.0)
            return {"action": "cancelled", **_status_dict(trade)}
    # Help the caller who passed a perm_id of an externally-placed order.
    for trade in trades:
        if trade.order.permId == order_id:
            raise ValueError(
                f"order perm_id {order_id} was placed via another channel "
                f"(client_id {trade.order.clientId}); cancel it where it was placed"
            )
    raise ValueError(f"no open order with id {order_id} placed by this CLI")


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


def execute_close(ib, account: str | None, token: str) -> dict:
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
        order = _limit_order(account, o["action"], o["qty"], o["limit"], o["tif"])
        results.append({"contract": o["localSymbol"], "action": o["action"],
                        **_place(ib, contract, order)})
    return {"action": "closed", "mode": params["mode"], "account": account, "orders": results}


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
    return [
        {
            "order_id": t.order.orderId,
            "contract": t.contract.localSymbol or t.contract.symbol or str(t.contract.conId),
            "side": t.order.action,
            "qty": num(t.order.totalQuantity),
            "type": t.order.orderType,
            "limit": num(t.order.lmtPrice),
            "tif": t.order.tif,
            "status": t.orderStatus.status,
            # account-wide, stable id (same across channels) — use this to
            # cross-reference with the Portal; cancel only works on our own.
            "perm_id": t.order.permId,
            # 0 = placed outside this CLI (web Portal, TWS, mobile, other client)
            "client_id": t.order.clientId,
        }
        for t in _all_open_trades(ib)
    ]


def list_trades(ib) -> list[dict]:
    # Unlike positions/orders, fills are not account-wide on the TWS API: this
    # returns executions visible to the API session for the current day. For
    # full account trade history use the Portal or Flex Queries.
    ib.reqExecutions()
    ib.sleep(1.0)
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
