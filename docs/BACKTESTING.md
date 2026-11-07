# Backtesting (research only)

Run from the repository root after installing the package:

```bash
python -m pip install -e .
python -m xau_intel.cli backtest --csv data/XAUUSD_M1.csv
```

Outputs:
- `output/trades.csv`: individual historical simulated trades by independent hypothesis.
- `output/backtest_report.json`: trade count, win rate, net price PnL, profit factor,
  expectancy in R and drawdown in R, separately for each hypothesis and a combined
  diagnostic total.

Optional execution-cost inputs:

```bash
python -m xau_intel.cli backtest --csv data/XAUUSD_M1.csv \
  --slippage-per-side 0.03 --commission-per-side 0.00
```

The values above are examples only, not broker-confirmed costs. Do not use them as
realistic settings without measuring the actual account's fills and commission.

## Execution assumptions and limitations

- A signal is confirmed at candle close and can enter no earlier than the next candle open.
- The CSV `spread` field is assumed to be an absolute XAUUSD price amount. Verify this
  against the Exness instrument specification before interpreting any PnL.
- Spread is charged half at entry and half at exit. Slippage and commission are
  optional price-unit equivalents per side.
- If stop and target are both touched inside one M1 candle, the stop is assumed first.
- A gap through a stop/target is filled at the candle open, not at the requested level.
- Each hypothesis is tested independently, with at most one position open per hypothesis.
  The combined row is a diagnostic sum and is NOT a portfolio simulation.
- Current defaults (1 ATR stop, 1.5R target, 60-bar time stop) are baseline test
  parameters, not optimized or validated trading settings.
- This engine does not yet model swap, financing, broker minimum stop distances,
  volume/contract sizing, latency, partial fills, or account-currency conversion.
- No result from this simulator authorizes demo or live trading.

Run unit tests:

```bash
python -m unittest discover -s tests -v
```
