"""ibkr CLI. JSON on stdout. Exit codes: 0 ok, 2 gateway unreachable,
3 validation/contract error, 4 token error.

Defaults to paper mode; live trading requires --live.
"""

import argparse
import json
import logging
import sys

logging.getLogger("ib_async").setLevel(logging.CRITICAL)

from . import orders
from .config import load_config
from .conn import GatewayUnreachable, connect
from .market import ContractNotFound, get_ticker, option_chain, quote_fields, resolve_option, resolve_stock
from .tokens import TokenError


def _fail(code: int, error: str, hint: str | None = None) -> int:
    out = {"error": error}
    if hint:
        out["hint"] = hint
    print(json.dumps(out, indent=2))
    return code


def _norm_expiry(value: str) -> str:
    digits = value.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError(f"expiry must be YYYY-MM-DD or YYYYMMDD, got {value!r}")
    return digits


def _norm_right(value: str) -> str:
    mapping = {"C": "C", "CALL": "C", "P": "P", "PUT": "P"}
    try:
        return mapping[value.upper()]
    except KeyError:
        raise ValueError(f"right must be C/CALL or P/PUT, got {value!r}") from None


def _norm_side(value: str) -> str:
    side = value.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {value!r}")
    return side


def _single_params(args, mode: str) -> dict:
    return {
        "kind": "single",
        "mode": mode,
        "symbol": args.symbol.upper(),
        "expiry": _norm_expiry(args.expiry),
        "strike": float(args.strike),
        "right": _norm_right(args.right),
        "side": _norm_side(args.side),
        "qty": int(args.qty),
        "limit": float(args.limit),
        "tif": args.tif,
    }


def _vertical_params(args, mode: str) -> dict:
    if float(args.long_strike) == float(args.short_strike):
        raise ValueError("long and short strikes must differ")
    return {
        "kind": "vertical",
        "mode": mode,
        "symbol": args.symbol.upper(),
        "expiry": _norm_expiry(args.expiry),
        "long_strike": float(args.long_strike),
        "short_strike": float(args.short_strike),
        "right": _norm_right(args.right),
        "side": _norm_side(args.side),
        "qty": int(args.qty),
        "limit": float(args.limit),
        "tif": args.tif,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ibkr", description=__doc__)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", dest="mode", action="store_const", const="live",
                            help="use the live account (default: paper)")
    mode_group.add_argument("--paper", dest="mode", action="store_const", const="paper")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="gateway reachability + account summary")
    sub.add_parser("positions", help="open positions")
    sub.add_parser("orders", help="open orders")
    sub.add_parser("trades", help="fills this session")

    p = sub.add_parser("chain", help="expirations, or strikes around spot for one expiry")
    p.add_argument("symbol")
    p.add_argument("--expiry")
    p.add_argument("--strikes", type=int, default=8)

    p = sub.add_parser("quote", help="quote a stock, or an option with --expiry/--strike/--right")
    p.add_argument("symbol")
    p.add_argument("--expiry")
    p.add_argument("--strike", type=float)
    p.add_argument("--right")

    for name in ("place", "place-vertical"):
        p = sub.add_parser(name, help=f"{name}: preview by default, trade only with --execute TOKEN")
        p.add_argument("--symbol", required=True)
        p.add_argument("--expiry", required=True)
        p.add_argument("--right", required=True)
        p.add_argument("--side", required=True)
        p.add_argument("--qty", required=True, type=int)
        p.add_argument("--limit", required=True, type=float,
                       help="limit price (net debit/credit for verticals), positive")
        p.add_argument("--tif", default="DAY", choices=["DAY", "GTC"])
        p.add_argument("--execute", metavar="TOKEN",
                       help="execute a previously previewed order; token must match")
        if name == "place":
            p.add_argument("--strike", required=True, type=float)
        else:
            p.add_argument("--long-strike", required=True, type=float)
            p.add_argument("--short-strike", required=True, type=float)

    p = sub.add_parser("cancel", help="cancel an open order")
    p.add_argument("order_id", type=int)

    return parser


def run(args) -> int:
    cfg = load_config(args.mode)
    mode = cfg["mode"]

    if args.command in ("place", "place-vertical"):
        if args.qty <= 0:
            raise ValueError("qty must be positive")
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        params = _single_params(args, mode) if args.command == "place" else _vertical_params(args, mode)

    ib = connect(cfg)
    try:
        if args.command == "status":
            out = {"mode": mode, "connected": True, **orders.account_summary(ib)}
        elif args.command == "positions":
            out = {"mode": mode, "positions": orders.list_positions(ib)}
        elif args.command == "orders":
            out = {"mode": mode, "orders": orders.list_open_orders(ib)}
        elif args.command == "trades":
            out = {"mode": mode, "trades": orders.list_trades(ib)}
        elif args.command == "chain":
            expiry = _norm_expiry(args.expiry) if args.expiry else None
            out = option_chain(ib, args.symbol.upper(), expiry, args.strikes)
        elif args.command == "quote":
            if args.expiry or args.strike or args.right:
                if not (args.expiry and args.strike and args.right):
                    raise ValueError("option quote needs all of --expiry, --strike, --right")
                contract = resolve_option(ib, args.symbol.upper(), _norm_expiry(args.expiry),
                                          args.strike, _norm_right(args.right))
            else:
                contract = resolve_stock(ib, args.symbol.upper())
            ticker, data_kind = get_ticker(ib, contract)
            out = {"contract": contract.localSymbol or contract.symbol,
                   "data": data_kind, **quote_fields(ticker)}
        elif args.command == "place":
            out = (orders.execute_single(ib, args.execute, params) if args.execute
                   else orders.preview_single(ib, params))
        elif args.command == "place-vertical":
            out = (orders.execute_vertical(ib, args.execute, params) if args.execute
                   else orders.preview_vertical(ib, params))
        elif args.command == "cancel":
            out = {"mode": mode, **orders.cancel_order(ib, args.order_id)}
        else:  # pragma: no cover
            raise ValueError(f"unknown command {args.command}")
    finally:
        ib.disconnect()

    print(json.dumps(out, indent=2))
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except GatewayUnreachable as exc:
        return _fail(2, "gateway_unreachable",
                     f"{exc} — launch IB Gateway and log in, then retry")
    except (ContractNotFound, ValueError) as exc:
        return _fail(3, str(exc))
    except TokenError as exc:
        return _fail(4, f"token rejected: {exc}")


if __name__ == "__main__":
    sys.exit(main())
