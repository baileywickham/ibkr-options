---
name: ibkr-options
description: Trade and research options on Interactive Brokers via a local CLI (chains, quotes, Greeks, single-leg and vertical orders). Use when the user asks about option chains, option quotes, or wants to place/cancel option orders on IBKR. Requires IB Gateway running locally. Paper account is the default; live needs --live.
---

# IBKR Options Trading

All commands: `uv run --project /Users/baileywickham/workspace/ibkr-options ibkr <command>`
Output is JSON. Exit codes: 0 ok, 2 gateway unreachable, 3 validation error, 4 token rejected.

**Mode**: paper account by default (Gateway port 4002). Add `--live` (port 4001) ONLY
when the user explicitly says to trade the live account.

## Commands

```
ibkr status                          # connection + account summary
ibkr positions | orders | trades
ibkr chain AAPL                      # expirations
ibkr chain AAPL --expiry 2026-07-17 --strikes 8   # strikes around spot, quotes + Greeks
ibkr quote AAPL                      # stock quote
ibkr quote AAPL --expiry 2026-07-17 --strike 200 --right C   # option quote
ibkr place --symbol AAPL --expiry 2026-07-17 --strike 200 --right C \
           --side BUY --qty 1 --limit 3.50        # PREVIEW (never trades)
ibkr place ... --execute TOKEN                    # place previewed order
ibkr place-vertical --symbol AAPL --expiry 2026-07-17 --right C --side BUY \
           --long-strike 200 --short-strike 205 --qty 1 --limit 1.80
ibkr cancel ORDER_ID
ibkr close 355C              # PREVIEW closing one position (match by symbol)
ibkr close --all            # PREVIEW closing every position
ibkr close 355C --execute TOKEN          # place the closing order(s)
ibkr close --all --limit 0.02            # override the closing limit price
```

Limit orders only; market orders are intentionally not implemented.
For verticals, `--limit` is the net debit (BUY) or net credit (SELL), always positive.

`close` builds an offsetting order per position (SELL to close a long, BUY to
close a short) priced marketably at the current bid/ask. There is no native
close-position call in the IBKR API — this replicates the TWS "Close" button.
Same preview→confirm→execute flow as place. If a position has no bid/ask quote
(e.g. a deep-OTM contract whose closing bid is negative), pass `--limit`. The
preview prints each position, its closing action, quantity, and limit, plus a
token; nothing is placed until you re-run with `--execute TOKEN`.

## Confirmation protocol (NON-NEGOTIABLE for live mode)

1. Run the `place`/`place-vertical` command WITHOUT `--execute`. This is a preview:
   it prints the resolved contract, current quotes, max loss/gain, and a `token`.
2. Show the user the preview (contract, side, qty, limit, max loss, premium).
3. LIVE MODE: wait for the user to explicitly approve THIS order in chat. Never
   infer approval from an earlier message, never batch approvals. Paper mode:
   self-confirmation is acceptable for testing.
4. Re-run the identical command with `--execute TOKEN`. The CLI rejects the token
   if any parameter changed, the preview is older than 5 minutes, or it was
   already used — in that case re-preview, re-confirm.

## Operational notes

- If you get `gateway_unreachable`: ask the user to launch IB Gateway
  (`open -a "IB Gateway 10.45"`) and log in (paper or live to match the mode).
  Do not attempt to enter credentials yourself — login is the user's job.
- First-time Gateway setup: in Configure → Settings → API → Settings, "Enable
  ActiveX and Socket Clients" must be on and "Read-Only API" must be OFF for
  order placement. Socket port: 4002 paper / 4001 live.
- `"data": "delayed"` in output means no realtime subscription for that
  instrument — quotes are 15-20 min old. Say so when showing the user numbers.
- Never provide personalized investment advice; research, data, and executing
  the user's decisions only.
