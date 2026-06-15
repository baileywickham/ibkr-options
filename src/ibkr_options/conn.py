"""Gateway connection helper. One connection per CLI invocation."""

from ib_async import IB


class GatewayUnreachable(Exception):
    pass


class AccountError(Exception):
    pass


def resolve_account(ib, cfg: dict) -> str:
    """The account to place orders against.

    Orders must pin an account: a login with more than one account (common once
    paper and live coexist, or sub-accounts exist) would otherwise route to an
    IBKR-chosen default. Uses the configured account if set, else the sole
    managed account, else errors and asks the user to choose.
    """
    accounts = [a for a in ib.managedAccounts() if a and a != "All"]
    want = cfg.get("account")
    if want:
        if want not in accounts:
            raise AccountError(f"configured account {want!r} not in this login's accounts {accounts}")
        return want
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise AccountError("no managed accounts returned by Gateway")
    raise AccountError(
        f"login has multiple accounts {accounts}; set account = \"<code>\" in "
        f"~/.ibkr-options/config.toml to choose which one orders go to"
    )


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
