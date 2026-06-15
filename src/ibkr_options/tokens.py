"""One-shot confirmation tokens binding an execute call to a previewed order.

A preview writes a pending file named by the token (sha256 of the canonical
order parameters). Execute must present a token that (a) matches the parameters
it was invoked with, (b) has a pending file, and (c) is younger than TTL.
The file is deleted on consumption, so a token can never be used twice.
"""

import hashlib
import json
import time
from pathlib import Path

PENDING_DIR = Path.home() / ".ibkr-options" / "pending"
TTL_SECONDS = 300


class TokenError(Exception):
    pass


def canonical(params: dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def make_token(params: dict) -> str:
    return hashlib.sha256(canonical(params).encode()).hexdigest()[:16]


def save_pending(params: dict, now: float | None = None, pending_dir: Path | None = None) -> str:
    pending_dir = pending_dir or PENDING_DIR
    pending_dir.mkdir(parents=True, exist_ok=True)
    token = make_token(params)
    payload = {"params": params, "created_at": now if now is not None else time.time()}
    (pending_dir / f"{token}.json").write_text(json.dumps(payload))
    return token


def consume(token: str, params: dict, now: float | None = None, pending_dir: Path | None = None) -> None:
    """Validate and burn a token. Raises TokenError unless everything matches.

    Used by place/place-vertical, where the caller re-supplies the order
    parameters (a tamper check: edited params produce a different hash).
    """
    pending_dir = pending_dir or PENDING_DIR
    now = now if now is not None else time.time()
    if make_token(params) != token:
        raise TokenError("order parameters do not match the previewed order for this token")
    path = pending_dir / f"{token}.json"
    if not path.exists():
        raise TokenError("no pending preview for this token (already used, or never previewed)")
    payload = _read_pending(path)
    path.unlink()
    if now - payload["created_at"] > TTL_SECONDS:
        raise TokenError("preview expired (>5 minutes old); run the preview again")


def _read_pending(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
        payload["created_at"]  # presence check
        return payload
    except (ValueError, KeyError) as exc:
        path.unlink(missing_ok=True)
        raise TokenError(f"pending preview file is corrupt: {exc}") from exc


def consume_stored(token: str, now: float | None = None, pending_dir: Path | None = None) -> dict:
    """Burn a token and return its stored params.

    Used by close, where the previewed plan (contracts, quantities, pinned
    limit prices) lives in the pending file; the token is the handle to it.
    The preview→see→execute flow still holds: nothing is placed until the user
    runs execute with the token they were shown.
    """
    pending_dir = pending_dir or PENDING_DIR
    now = now if now is not None else time.time()
    path = pending_dir / f"{token}.json"
    if not path.exists():
        raise TokenError("no pending preview for this token (already used, or never previewed)")
    payload = _read_pending(path)
    path.unlink()
    if now - payload["created_at"] > TTL_SECONDS:
        raise TokenError("preview expired (>5 minutes old); run the preview again")
    if "params" not in payload:
        raise TokenError("pending preview file is missing its order plan")
    return payload["params"]
