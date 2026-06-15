"""Account resolution: orders must pin a single, explicit account."""

import pytest

from ibkr_options.conn import AccountError, resolve_account


class FakeIB:
    def __init__(self, accounts):
        self._accounts = accounts

    def managedAccounts(self):
        return self._accounts


def test_sole_account_used_automatically():
    assert resolve_account(FakeIB(["DU123"]), {}) == "DU123"


def test_all_pseudo_account_is_ignored():
    assert resolve_account(FakeIB(["All", "DU123"]), {}) == "DU123"


def test_multiple_accounts_require_config():
    with pytest.raises(AccountError, match="multiple accounts"):
        resolve_account(FakeIB(["U111", "U222"]), {})


def test_configured_account_selected():
    assert resolve_account(FakeIB(["U111", "U222"]), {"account": "U222"}) == "U222"


def test_configured_account_must_exist():
    with pytest.raises(AccountError, match="not in this login"):
        resolve_account(FakeIB(["U111"]), {"account": "U999"})


def test_no_accounts_errors():
    with pytest.raises(AccountError, match="no managed accounts"):
        resolve_account(FakeIB([]), {})
