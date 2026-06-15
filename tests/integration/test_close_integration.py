"""Close-command integration tests: offsetting preview, and a real open→close
flatten cycle against the paper account."""

import pytest

from ibkr_options.orders import (
    execute_close,
    execute_single,
    preview_close,
    preview_single,
)

pytestmark = pytest.mark.integration


def _open_long(lab):
    """Open a 1-lot long ITM call; skip the test if paper doesn't fill."""
    opt = lab.resolve(lab.market["itm_call"], "C")
    _bid, ask = lab.quote(lab.market["itm_call"], "C")
    if ask is None:
        pytest.skip("no ask quote for marketable order")
    # one tick over the ask; a large buffer trips IBKR error 202 (too aggressive)
    params = lab.single_params(lab.market["itm_call"], "BUY", 1, round(ask + 0.05, 2))
    out = preview_single(lab.ib, params)
    execute_single(lab.ib, None, out["token"], params)
    if not lab.wait_position(opt.conId, timeout=10):
        pytest.skip("paper did not fill (market likely closed)")
    return opt


def test_close_no_positions_raises(lab):
    with pytest.raises(ValueError, match="no open positions"):
        preview_close(lab.ib, "paper", None, None, "DAY")


def test_close_preview_builds_offsetting_order(lab):
    opt = _open_long(lab)
    out = preview_close(lab.ib, "paper", None, None, "DAY")
    assert len(out["positions"]) == 1
    leg = out["positions"][0]
    assert leg["current_qty"] == 1.0
    assert leg["action"] == "SELL"  # offsetting a long
    assert leg["close_qty"] == 1.0
    assert out["token"]
    # preview must not change the position
    assert lab.position_qty(opt.conId) == 1.0


def test_close_all_flattens_account(lab):
    opt = _open_long(lab)
    out = preview_close(lab.ib, "paper", None, None, "DAY")
    execute_close(lab.ib, None, out["token"])
    # closing sells at the bid (marketable); poll for the position to clear
    cleared = False
    for _ in range(10):
        lab.ib.sleep(1)
        if lab.position_qty(opt.conId) == 0:
            cleared = True
            break
    if not cleared:
        pytest.skip("closing order did not fill (market likely closed)")
    assert lab.position_qty(opt.conId) == 0


def test_close_query_filters_to_matching_position(lab):
    _open_long(lab)
    # 'AAPL' matches; a bogus query matches nothing
    matched = preview_close(lab.ib, "paper", "AAPL", None, "DAY")
    assert len(matched["positions"]) == 1
    with pytest.raises(ValueError, match="no open position matching"):
        preview_close(lab.ib, "paper", "TSLA999", None, "DAY")
