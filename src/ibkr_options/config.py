"""Connection configuration.

Mode resolution order: --live/--paper CLI flag > IBKR_MODE env var > config file > paper.
Config file: ~/.ibkr-options/config.toml, e.g.

    mode = "paper"          # or "live"
    host = "127.0.0.1"
    client_id = 17
    [ports]
    paper = 4002
    live = 4001
"""

import os
import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".ibkr-options" / "config.toml"

DEFAULT_PORTS = {"paper": 4002, "live": 4001}


def load_config(mode: str | None = None) -> dict:
    file_cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            file_cfg = tomllib.loads(CONFIG_PATH.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"config file {CONFIG_PATH} is not valid TOML: {exc}") from exc

    resolved_mode = mode or os.environ.get("IBKR_MODE") or file_cfg.get("mode") or "paper"
    if resolved_mode not in ("paper", "live"):
        raise ValueError(f"invalid mode {resolved_mode!r}: must be 'paper' or 'live'")

    ports = {**DEFAULT_PORTS, **file_cfg.get("ports", {})}
    return {
        "mode": resolved_mode,
        "host": file_cfg.get("host", "127.0.0.1"),
        "port": ports[resolved_mode],
        "client_id": file_cfg.get("client_id", 17),
        "timeout": file_cfg.get("timeout", 5.0),
        # 1=realtime, 3=delayed. Default delayed: works without a market-data
        # subscription. Set to 1 in config.toml once you subscribe.
        "market_data_type": file_cfg.get("market_data_type", 3),
        # Account to place orders against. None = auto (sole managed account).
        # Required when the login has more than one account.
        "account": file_cfg.get("account"),
        # Permit live orders priced off delayed data without --allow-delayed.
        # Opt-in: only set true if you knowingly accept delayed quotes (e.g.
        # buy-and-hold with limits you set yourself). Previews still warn.
        "allow_delayed_live": file_cfg.get("allow_delayed_live", False),
    }
