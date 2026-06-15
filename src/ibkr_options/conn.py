"""Gateway connection helper. One connection per CLI invocation."""

from ib_async import IB


class GatewayUnreachable(Exception):
    pass


def connect(cfg: dict) -> IB:
    ib = IB()
    try:
        ib.connect(
            cfg["host"],
            cfg["port"],
            clientId=cfg["client_id"],
            timeout=cfg["timeout"],
        )
    except Exception as exc:
        raise GatewayUnreachable(
            f"could not reach IB Gateway at {cfg['host']}:{cfg['port']} ({cfg['mode']} mode): {exc}"
        ) from exc
    # Set the market-data type once per connection. Toggling it per request
    # triggers IBKR pacing errors (300/322), so we fix it here.
    ib.reqMarketDataType(cfg.get("market_data_type", 3))
    return ib
