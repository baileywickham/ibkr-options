"""The live delayed-data guard: refuse to price a live order off delayed data
unless explicitly overridden."""

import pytest

from ibkr_options.cli import DelayedDataBlock, _guard_live_delayed


def test_live_delayed_blocked():
    with pytest.raises(DelayedDataBlock):
        _guard_live_delayed({"mode": "live", "market_data_type": 3}, allow_delayed=False)


def test_live_delayed_allowed_with_override():
    _guard_live_delayed({"mode": "live", "market_data_type": 3}, allow_delayed=True)


def test_live_realtime_ok():
    _guard_live_delayed({"mode": "live", "market_data_type": 1}, allow_delayed=False)


def test_paper_delayed_ok():
    # paper never blocks; delayed data there is fine for testing
    _guard_live_delayed({"mode": "paper", "market_data_type": 3}, allow_delayed=False)
