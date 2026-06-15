"""Regression: ib_async returns [None] (not []) for an unqualifiable contract.
resolve_* must treat that as 'not found', never return None."""

import pytest

from ibkr_options.market import ContractNotFound, resolve_option, resolve_stock


class IBReturningNone:
    """Mimics ib_async qualifyContracts failing: returns [None]."""

    def qualifyContracts(self, contract):
        return [None]


def test_resolve_option_treats_none_as_not_found():
    with pytest.raises(ContractNotFound, match="no option contract"):
        resolve_option(IBReturningNone(), "AAPL", "20260717", 295.77, "C")


def test_resolve_stock_treats_none_as_not_found():
    with pytest.raises(ContractNotFound, match="no US stock"):
        resolve_stock(IBReturningNone(), "ZZZZ")
