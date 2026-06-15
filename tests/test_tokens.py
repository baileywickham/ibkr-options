import pytest

from ibkr_options import tokens


PARAMS = {"kind": "single", "mode": "paper", "symbol": "AAPL", "expiry": "20260717",
          "strike": 200.0, "right": "C", "side": "BUY", "qty": 1, "limit": 3.5, "tif": "DAY"}


def test_token_is_deterministic_and_param_order_independent():
    shuffled = dict(reversed(list(PARAMS.items())))
    assert tokens.make_token(PARAMS) == tokens.make_token(shuffled)


def test_different_params_different_token():
    tweaked = {**PARAMS, "qty": 2}
    assert tokens.make_token(PARAMS) != tokens.make_token(tweaked)


def test_consume_happy_path(tmp_path):
    tok = tokens.save_pending(PARAMS, now=1000.0, pending_dir=tmp_path)
    tokens.consume(tok, PARAMS, now=1010.0, pending_dir=tmp_path)


def test_consume_is_one_shot(tmp_path):
    tok = tokens.save_pending(PARAMS, now=1000.0, pending_dir=tmp_path)
    tokens.consume(tok, PARAMS, now=1010.0, pending_dir=tmp_path)
    with pytest.raises(tokens.TokenError, match="already used"):
        tokens.consume(tok, PARAMS, now=1020.0, pending_dir=tmp_path)


def test_consume_rejects_tampered_params(tmp_path):
    tok = tokens.save_pending(PARAMS, now=1000.0, pending_dir=tmp_path)
    with pytest.raises(tokens.TokenError, match="do not match"):
        tokens.consume(tok, {**PARAMS, "limit": 9.5}, now=1010.0, pending_dir=tmp_path)
    # the pending file survives a mismatched attempt
    tokens.consume(tok, PARAMS, now=1020.0, pending_dir=tmp_path)


def test_consume_rejects_expired(tmp_path):
    tok = tokens.save_pending(PARAMS, now=1000.0, pending_dir=tmp_path)
    with pytest.raises(tokens.TokenError, match="expired"):
        tokens.consume(tok, PARAMS, now=1000.0 + tokens.TTL_SECONDS + 1, pending_dir=tmp_path)


def test_consume_rejects_never_previewed(tmp_path):
    with pytest.raises(tokens.TokenError, match="never previewed|already used"):
        tokens.consume(tokens.make_token(PARAMS), PARAMS, now=1000.0, pending_dir=tmp_path)


def test_corrupt_pending_file_raises_tokenerror_not_traceback(tmp_path):
    tok = tokens.make_token(PARAMS)
    (tmp_path / f"{tok}.json").write_text("{not valid json")
    with pytest.raises(tokens.TokenError, match="corrupt"):
        tokens.consume(tok, PARAMS, now=1000.0, pending_dir=tmp_path)
    # corrupt file is removed so it can't wedge future calls
    assert not (tmp_path / f"{tok}.json").exists()


def test_consume_stored_corrupt_file_raises_tokenerror(tmp_path):
    tok = "deadbeef"
    (tmp_path / f"{tok}.json").write_text('{"no_created_at": true}')
    with pytest.raises(tokens.TokenError, match="corrupt"):
        tokens.consume_stored(tok, now=1000.0, pending_dir=tmp_path)
