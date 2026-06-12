"""Risk math for two-leg vertical spreads.

`limit` is the net price of the spread, always positive: a debit when side is
BUY, a credit when side is SELL. `width` is the distance between strikes.
"""


def vertical_risk(side: str, qty: int, limit: float, width: float) -> dict:
    if limit <= 0:
        raise ValueError("limit (net debit/credit) must be positive")
    if width <= 0:
        raise ValueError("strike width must be positive")
    if side == "BUY":
        if limit >= width:
            raise ValueError(f"debit {limit} >= width {width}: spread can never profit")
        max_loss = limit * 100 * qty
        max_gain = (width - limit) * 100 * qty
    elif side == "SELL":
        if limit >= width:
            raise ValueError(f"credit {limit} >= width {width}: more credit than the spread is wide")
        max_gain = limit * 100 * qty
        max_loss = (width - limit) * 100 * qty
    else:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    return {"max_loss_usd": round(max_loss, 2), "max_gain_usd": round(max_gain, 2)}
