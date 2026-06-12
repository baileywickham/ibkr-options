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
    return ib
