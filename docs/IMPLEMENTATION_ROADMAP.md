# Integrated XAUUSD Market Intelligence Roadmap

## Objective and non-claims
Build a reproducible research and decision-support system for XAUUSD. OHLCV bars can reveal price-action proxies; they cannot reveal actual stop orders, institutional identities, hidden liquidity, or full order flow. Never label a pattern as proven manipulation or institutional activity.

## Architecture
1. Immutable raw data plus schema, timestamp and broker-feed validation.
2. Gap-aware segmentation; no rolling feature, pivot, ATR or FVG may bridge a missing interval.
3. Causal feature layer: ATR/volatility regimes, Dubai/London/New York sessions, prior day/week levels, confirmed swings, equal-level candidates, liquidity sweeps, displacement and FVG.
4. Event layer: timestamp events only when observable; store level, excursion, confirmation, context and subsequent reaction separately.
5. Hypothesis registry: one hypothesis per testable mechanism; no silent parameter search on the final holdout.
6. Execution-aware backtest: bid/ask side, spread, commission, swap, slippage, latency, gaps, stop distance, partial/rejected fills and conservative intrabar ordering.
7. Evaluation: chronological train/validation/locked test, walk-forward, benchmark/random controls, confidence intervals, multiple-testing correction, regime and session breakdown.
8. Portfolio/risk: chronological single portfolio, correlated exposure, sizing, leverage/margin, daily loss and drawdown kill-switch.
9. Broker adapter: read-only discovery/config first; demo order state machine with idempotency and recovery; live remains disabled until explicit approval.
10. Monitoring/reporting: reproducible reports, data lineage, model/version metadata, alerts and audit logs.

## Implemented in this change
- market_intelligence.py: strict OHLCV validation, gap segmentation, segment-reset ATR and rolling levels, right-bar-confirmed pivots, equal-level candidate counts, swing sweep candidates, displacement, three-candle FVG candidates, and DST-aware London/New York session labels.
- Tests cover gap segmentation, invalid OHLC, causal pivot confirmation, FVG gap isolation and negative volume.

## Remaining release gates
- Verify broker timestamp semantics and CSV spread units.
- Add previous day/week liquidity levels and richer equal-high/low clustering.
- Add MSS/CHOCH and sweep-to-displacement/retest event chains.
- Add causal gap-reset behavior to all legacy feature and backtest paths.
- Correct portfolio chronology and validate fill/risk accounting with independent fixtures.
- Add walk-forward, locked holdout, benchmark and statistical uncertainty reports.
- Confirm exact Exness API documentation, host discovery and demo permissions before any order adapter.
- Implement kill-switch, idempotency, reconciliation, reconnect and audit trail before demo forward tests.
- No live trading until all release gates pass and user explicitly authorizes it.

## Research interpretation
Liquidity pools and manipulation are hypotheses inferred from observed prices, not direct observations of resting orders or intent.
