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
    ticker, data_kind = get_ticker(ib, opt)
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
