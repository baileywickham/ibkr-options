"""ibkr CLI. JSON on stdout. Exit codes: 0 ok, 2 gateway unreachable,
3 validation/contract error, 4 token error, 5 account error,
6 delayed-data guard (live order blocked).

Defaults to paper mode; live trading requires --live.
"""

import argparse
import json
import logging
import sys

logging.getLogger("ib_async").setLevel(logging.CRITICAL)

from . import market, orders
from .config import load_config
from .conn import AccountError, GatewayUnreachable, connect, resolve_account
from .market import ContractNotFound, get_ticker, option_chain, quote_fields, resolve_option, resolve_stock
from .tokens import TokenError


class DelayedDataBlock(Exception):
    """Raised when a live order would be priced off delayed market data."""


_TRADING_EXECUTE = {"place", "place-vertical", "stock", "close"}


def _guard_live_delayed(cfg: dict, allow_delayed: bool) -> None:
    permitted = allow_delayed or cfg.get("allow_delayed_live", False)
    if cfg["mode"] == "live" and cfg.get("market_data_type", 3) == 3 and not permitted:
        raise DelayedDataBlock(
            "refusing to place a LIVE order priced off delayed (~15 min) market "
            "data. Subscribe to real-time data and set market_data_type = 1 in "
            "~/.ibkr-options/config.toml, or pass --allow-delayed to override."
        )


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


def _stock_params(args, mode: str) -> dict:
    return {
        "kind": "stock",
        "mode": mode,
        "symbol": args.symbol.upper(),
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
        p.add_argument("--allow-delayed", action="store_true",
                       help="permit a live order priced off delayed data")
        if name == "place":
            p.add_argument("--strike", required=True, type=float)
        else:
            p.add_argument("--long-strike", required=True, type=float)
            p.add_argument("--short-strike", required=True, type=float)

    p = sub.add_parser("stock", help="trade shares: preview by default, --execute TOKEN to place")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True)
    p.add_argument("--qty", required=True, type=int)
    p.add_argument("--limit", required=True, type=float, help="limit price per share, positive")
    p.add_argument("--tif", default="DAY", choices=["DAY", "GTC"])
    p.add_argument("--execute", metavar="TOKEN", help="execute a previewed order; token must match")
    p.add_argument("--allow-delayed", action="store_true",
                   help="permit a live order priced off delayed data")

    p = sub.add_parser("cancel", help="cancel an open order")
    p.add_argument("order_id", type=int)

    p = sub.add_parser("close", help="close (offset) a position with a marketable limit; "
                                     "preview by default, --execute TOKEN to place")
    p.add_argument("query", nargs="?", help="match against a position's symbol; omit when using --all")
    p.add_argument("--all", action="store_true", help="close every open position")
    p.add_argument("--limit", type=float, help="override the closing limit price (per contract)")
    p.add_argument("--tif", default="DAY", choices=["DAY", "GTC"])
    p.add_argument("--execute", metavar="TOKEN", help="execute a previewed close")
    p.add_argument("--allow-delayed", action="store_true",
                   help="permit a live order priced off delayed data")

    return parser


def run(args) -> int:
    cfg = load_config(args.mode)
    mode = cfg["mode"]

    if args.command in ("place", "place-vertical", "stock"):
        if args.qty <= 0:
            raise ValueError("qty must be positive")
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        params = {
            "place": _single_params,
            "place-vertical": _vertical_params,
            "stock": _stock_params,
        }[args.command](args, mode)

    # One source of truth for "this invocation places an order": the guard,
    # account pinning, and the execute/preview dispatch must never disagree.
    executing = args.command in _TRADING_EXECUTE and bool(getattr(args, "execute", None))

    ib = connect(cfg)
    try:
        # Placing an order: enforce the delayed-data guard and pin the account.
        account = None
        if executing:
            _guard_live_delayed(cfg, getattr(args, "allow_delayed", False))
            account = resolve_account(ib, cfg)
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
                ticker, dk = get_ticker(ib, contract, want_greeks=True)
                out = {"contract": contract.localSymbol or contract.symbol,
                       "data": dk, **quote_fields(ticker)}
            else:
                stk = resolve_stock(ib, args.symbol.upper())
                out = {"contract": stk.symbol, "data": market.data_kind(ib),
                       "last_close": market.spot_price(ib, stk)}
        elif args.command == "place":
            out = (orders.execute_single(ib, account, args.execute, params) if executing
                   else orders.preview_single(ib, params))
        elif args.command == "place-vertical":
            out = (orders.execute_vertical(ib, account, args.execute, params) if executing
                   else orders.preview_vertical(ib, params))
        elif args.command == "stock":
            out = (orders.execute_stock(ib, account, args.execute, params) if executing
                   else orders.preview_stock(ib, params))
        elif args.command == "cancel":
            out = {"mode": mode, **orders.cancel_order(ib, args.order_id)}
        elif args.command == "close":
            if executing:
                out = orders.execute_close(ib, account, args.execute)
            else:
                if args.all and args.query:
                    raise ValueError("pass either a position query or --all, not both")
                if not args.all and not args.query:
                    raise ValueError("specify a position query or --all")
                query = None if args.all else args.query
                out = orders.preview_close(ib, mode, query, args.limit, args.tif)
        else:  # pragma: no cover
            raise ValueError(f"unknown command {args.command}")

        # Loud reminder on any preview priced off delayed data.
        is_preview = args.command in _TRADING_EXECUTE and not executing
        if is_preview and cfg.get("market_data_type", 3) == 3:
            out["warning"] = "prices are DELAYED ~15 min; verify against a live quote before trading"
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
    except AccountError as exc:
        return _fail(5, f"account error: {exc}")
    except DelayedDataBlock as exc:
        return _fail(6, "delayed_data_blocked", str(exc))


if __name__ == "__main__":
    sys.exit(main())
