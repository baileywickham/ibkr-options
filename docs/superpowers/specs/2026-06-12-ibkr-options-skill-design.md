# IBKR Options Skill — Design

Date: 2026-06-12
Status: Approved (live account, single-leg + verticals, chat-confirmation-only, manual Gateway launch)

## Goal

A Claude Code skill that lets Claude research and trade options on the user's live
Interactive Brokers account via `ib_async` and IB Gateway, with a CLI-enforced
preview→confirm→execute flow so no order can be placed without the user seeing and
approving exactly that order in chat.

## Decisions

- **Account**: both paper and live supported; **paper is the default** and live
  requires an explicit `--live` flag (revised per user 2026-06-12). Gateway ports:
  paper 4002, live 4001.
- **Scope**: single-leg calls/puts and two-leg vertical spreads (native combo/BAG orders).
- **Guardrails**: chat confirmation is the only gate. No max-risk caps, no naked-short
  blocking, no daily limits. Limit orders only — market orders are not implemented.
- **Gateway**: user launches and logs into IB Gateway manually (live port 4001).
  The CLI connects when it's up and fails with a clear message when it isn't.
- **Confirmation policy**: live orders always require explicit user confirmation of
  the preview in chat. Paper orders may be self-confirmed by Claude for testing.

## Architecture

uv-managed Python package at `~/workspace/ibkr-options`, dependency `ib_async`.
One CLI entry point: `uv run ibkr <command>`, all output JSON. Stateless: each command
connects to Gateway, acts, disconnects. `config.toml` holds host/port/client_id.
`SKILL.md` installed at `~/.claude/skills/ibkr-options/` documents usage and the
confirmation rules for Claude.

### Commands

| Command | Action |
|---|---|
| `status` | Gateway reachability + account summary |
| `chain SYM [--expiry E] [--strikes N]` | expirations, or strikes around spot with bid/ask + Greeks |
| `quote` | stock or option quote |
| `positions` / `orders` / `trades` | account state |
| `place` | single-leg preview / execute |
| `place-vertical` | two-leg vertical preview / execute |
| `cancel ID` | cancel open order |
| `close QUERY` / `close --all` | flatten position(s) with offsetting marketable limit orders (preview/execute) |

### Confirmation mechanism

Two-phase, enforced by the CLI:

1. `place …` (no flag) resolves the contract, fetches bid/ask/mid, computes max
   loss/gain for verticals, prints a preview JSON, and writes a one-shot pending-order
   file keyed by a token = sha256 of the canonical order parameters.
2. `place … --execute TOKEN` re-canonicalizes the parameters, requires the token to
   match and the pending file to exist and be < 5 minutes old, deletes it, and places
   the order. Mismatched, missing, expired, or reused tokens are hard errors.

Claude's workflow (encoded in SKILL.md): run preview → show the user the preview →
wait for an explicit yes → run execute with the token. Never run execute without a
fresh user confirmation of that specific preview.

### Vertical math

- Debit vertical: max loss = debit × 100 × qty; max gain = (width − debit) × 100 × qty.
- Credit vertical: max gain = credit × 100 × qty; max loss = (width − credit) × 100 × qty.

## Error handling

- Gateway down → exit 2 with `{"error": "gateway_unreachable", …}` and a human hint
  to launch and log in. No retry loops.
- Ambiguous contract → error listing candidate contracts; never guess.
- Missing/empty market data → preview still renders but carries
  `"data": "delayed"` or `"data": "missing"` flags; delayed fallback via
  `reqMarketDataType(3)` when realtime isn't subscribed.

## Testing

- Unit (no IB): token canonicalization/expiry/one-shot semantics, vertical max-loss
  math, order construction (incl. combo legs), CLI preview-never-places invariant.
- Integration (Gateway logged in, read-only): status, chain, quote, positions.
- Live order path: 1-contract far-OTM limit order, user-confirmed, then cancelled.

## Out of scope (for now)

Market orders, multi-leg beyond verticals, streaming/monitoring, auto-started
Gateway (IBC), paper account profile (config supports changing port later).
