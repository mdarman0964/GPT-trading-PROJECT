# Release gates

- [ ] Confirm CSV timezone and broker candle timestamp semantics.
- [ ] Re-audit the latest complete dataset and classify market-closure gaps.
- [ ] Confirm XAUUSD tick size, tick value, lot min/max/step and stop-distance rules.
- [ ] Define strategy hypotheses without look-ahead bias.
- [ ] Run chronological train/validation/out-of-sample tests.
- [ ] Model spread, commission, swap, slippage and rejected/partial fills.
- [ ] Run walk-forward analysis and stress tests.
- [ ] Confirm API host, Ed25519 key format, idempotency and asynchronous order status handling.
- [ ] Validate demo execution, reconnect recovery and duplicate-order prevention.
- [ ] Enforce max risk/trade, daily loss, exposure and spread limits.
- [ ] Obtain explicit approval before enabling live orders.
