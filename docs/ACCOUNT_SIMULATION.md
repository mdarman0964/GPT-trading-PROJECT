# USD account simulation

The `account-backtest` command converts the project's existing price-based
historical trades into a USD-denominated, risk-sized account simulation.

Example (only after confirming the exact instrument contract size and account leverage):
```bash
python -m xau_intel.cli account-backtest --csv data/XAUUSD_M1.csv \
  --initial-balance 1000 --risk-percent 1 \
  --contract-size 100 --leverage 100
```

The example values for contract size and leverage are placeholders, not an
assertion about a particular Exness account. Supply values from the exact
account/instrument specification. The simulation writes account trades,
equity curve, and a JSON report under `output/`.

Rules and limitations:
- Risk is a fraction of current balance; default is 1% per trade.
- Lots are rounded down to the configured lot step.
- Estimated margin is lots × contract size × entry price ÷ leverage.
- Only one portfolio position is allowed at a time; overlapping candidate
  trades are skipped in entry-index order.
- Spread and configured price-unit costs are included by the base backtester.
- Optional USD round-turn commission is deducted separately.
- Swap, currency conversion, broker stop-out, partial fills, latency, and
  broker-specific margin rules are not modeled.
- This is historical simulation only; it does not place demo or live orders.
- Profitability cannot be evaluated until the actual CSV is supplied and the
  contract size, leverage, lot constraints, and commission are verified.
