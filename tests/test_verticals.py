import pytest

from ibkr_options.verticals import vertical_risk


def test_debit_vertical():
    # buy 1x 200/205 call spread for 1.80 debit
    risk = vertical_risk("BUY", 1, 1.80, 5.0)
    assert risk == {"max_loss_usd": 180.0, "max_gain_usd": 320.0}


def test_credit_vertical():
    # sell 2x 195/190 put spread for 1.25 credit
    risk = vertical_risk("SELL", 2, 1.25, 5.0)
    assert risk == {"max_gain_usd": 250.0, "max_loss_usd": 750.0}


def test_debit_exceeding_width_rejected():
    with pytest.raises(ValueError, match="never profit"):
        vertical_risk("BUY", 1, 5.5, 5.0)


def test_credit_exceeding_width_rejected():
    with pytest.raises(ValueError, match="more credit"):
        vertical_risk("SELL", 1, 5.5, 5.0)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_nonpositive_limit_rejected(bad):
    with pytest.raises(ValueError):
        vertical_risk("BUY", 1, bad, 5.0)
