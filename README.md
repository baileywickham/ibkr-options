# ibkr-options

A small command-line tool for researching and trading **options and stocks** on
[Interactive Brokers](https://www.interactivebrokers.com/) from your terminal,
built on [`ib_async`](https://github.com/ib-api-reloaded/ib_async) and IB Gateway.

Every order goes through a **preview → confirm → execute** flow: a preview prints
the exact order and a one-shot token, and nothing is placed until you re-run with
that token. Output is JSON, so it composes well with other tools and with agents.

> ## ⚠️ Risk disclaimer — read this
>
> This software places **real orders with real money** against your brokerage
> account. It is provided **as-is, with no warranty**, under the MIT License.
> Bugs, network failures, stale quotes, or misuse can lose money.
>
> - It is **not** investment advice. You are solely responsible for every order.
> - **Test on a paper account first** (the default mode).
> - Options trading carries substantial risk and is not suitable for everyone.
> - The authors accept no liability for any financial loss. Use at your own risk.

## Features

- Option **chains** with bid/ask, mid, and Greeks; stock and option **quotes**.
- **Single-leg** option orders and **vertical spreads** (native combo orders).
- **Stock** orders (shares).
- **Close** positions with offsetting marketable-limit orders (`close`/`close --all`).
- Account state: `status`, `positions`, `orders`, `trades`.
- Limit orders only — market orders are intentionally not implemented.

## Safety model

- **Preview → token → execute.** A preview places nothing and returns a one-shot
  token (a hash of the exact order parameters). Execute requires that token; it is
  rejected if any parameter changed, the preview is older than 5 minutes, or it was
  already used.
- **Account pinning.** Every order pins an account. Single-account logins resolve
  automatically; multi-account logins must set `account` in config or the order fails.
- **Delayed-data guard.** Live orders priced off delayed (~15 min) data are refused
  unless you opt in (`allow_delayed_live` or `--allow-delayed`). Paper is never blocked.
- **Rejection surfacing.** A rejected order returns `"rejected": true` with the reason
  (e.g. `[202] Limit price too far outside of NBBO`); an order IBKR never acknowledges
  is flagged `"unconfirmed": true`. A non-error status is never reported as success.

## Requirements

- An **IBKR Pro** account (Lite has no API access).
- **IB Gateway** (or TWS) installed and logged in, with the API socket enabled.
- **Python 3.11+** and [`uv`](https://github.com/astral-sh/uv).

## Install

```bash
git clone <your-fork-url> ibkr-options && cd ibkr-options
uv tool install --editable .     # puts `ibkr` on your PATH
```

(Or run without installing: `uv run ibkr <command>` from the repo.)

## Gateway setup

1. Launch IB Gateway and log in. Paper trading uses **port 4002**, live uses **4001**.
2. In **Configure → Settings → API → Settings**: enable *ActiveX and Socket Clients*,
   and **uncheck** *Read-Only API* (so orders can be placed).
3. The CLI connects when Gateway is up and fails with a clear message when it isn't.

## Configuration

Optional config at `~/.ibkr-options/config.toml` (the repo never reads a local file):

```toml
mode = "paper"             # default when neither --paper nor --live is passed
market_data_type = 3       # 3 = delayed (no subscription needed), 1 = realtime
allow_delayed_live = false # set true to permit live orders on delayed data
# account = "U1234567"     # required only if your login has multiple accounts
```

## Usage

Mode defaults to paper; pass `--live` to act on the live account.

```bash
ibkr status                                   # connection + account summary
ibkr positions | ibkr orders | ibkr trades

# Research
ibkr chain AAPL                               # expirations
ibkr chain AAPL --expiry 2026-07-17 --strikes 8   # strikes near spot + Greeks
ibkr quote AAPL                               # stock quote
ibkr quote AAPL --expiry 2026-07-17 --strike 200 --right C

# Single-leg option (preview, then execute with the returned token)
ibkr place --symbol AAPL --expiry 2026-07-17 --strike 200 --right C \
           --side BUY --qty 1 --limit 3.50
ibkr place ... --execute <TOKEN>

# Vertical spread (--limit is net debit for BUY, net credit for SELL)
ibkr place-vertical --symbol AAPL --expiry 2026-07-17 --right C --side BUY \
           --long-strike 200 --short-strike 205 --qty 1 --limit 1.80

# Stock
ibkr stock --symbol AAPL --side BUY --qty 10 --limit 250

# Manage
ibkr cancel <ORDER_ID>
ibkr close 200C            # close one position (match by symbol)
ibkr close --all          # close everything
```

Exit codes: `0` ok, `2` gateway unreachable, `3` validation/contract error,
`4` token rejected, `5` account error, `6` delayed-data blocked.

## Testing

```bash
uv run pytest -m "not integration"   # unit tests, no Gateway needed
uv run pytest -m integration         # integration tests; needs a logged-in PAPER Gateway
uv run pytest                        # both (integration auto-skips if Gateway is down)
```

Integration tests run against a paper account, discover contracts dynamically so they
don't rot, flatten the account before/after each order test, and skip (not fail) if the
paper engine doesn't fill.

## License

MIT — see [LICENSE](LICENSE). Built on `ib_async` (BSD). Not affiliated with
Interactive Brokers.
