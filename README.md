# XAUUSD Market Intelligence System

Research foundation for analyzing the supplied XAUUSD M1 history and integrating with the supplied Exness Trader API specification.

## Current status

This is an **offline research foundation**, not a proven profitable strategy and not a live trading bot. Live order execution is intentionally not implemented until strategy rules, instrument contract details, risk limits, API host/key format, and demo-forward validation are confirmed.

## Included capabilities

- CSV integrity audit
- OHLC and spread validation
- ATR, rolling high/low, returns and spread-to-volatility features
- Reproducible command-line research tools
- USD-denominated account simulation with risk-based lot sizing, estimated margin, equity curve and drawdown
- API integration and validation roadmap

## Setup

Python 3.10+

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Run

```bash
PYTHONPATH=src python -m xau_intel.cli audit --csv data/hist_XAUUSD_M1_a.csv
PYTHONPATH=src python -m xau_intel.cli features --csv data/hist_XAUUSD_M1_a.csv --out reports/features.csv

# Requires contract size and leverage verified for the exact broker account/instrument
PYTHONPATH=src python -m xau_intel.cli account-backtest --csv data/hist_XAUUSD_M1_a.csv \
  --initial-balance 1000 --risk-percent 1 --contract-size YOUR_VERIFIED_CONTRACT_SIZE \
  --leverage YOUR_ACCOUNT_LEVERAGE
```

## Data notes

The current supplied dataset contains 353,307 XAUUSD M1 candles from 2025-11-07 04:20 to 2026-11-06 21:57 as stored in the file. Timestamps have no declared timezone. There are 275 gaps greater than one minute; these must be classified before session analysis. Spread median is 0.24; 99.9th percentile is 0.60. CSV rows should be audited again when the dataset changes.

See [USD account simulation assumptions](docs/ACCOUNT_SIMULATION.md). The account simulation
uses one global position at a time and skips overlapping candidates. Contract size and
leverage are mandatory CLI inputs; do not use example values without verifying them
against the exact Exness instrument and account specification. It remains a historical
simulation and does not send demo or live orders.

## Before enabling execution

1. Define testable entry/exit hypotheses and position management.
2. Confirm timezone, tick size/value, lot size constraints and trading hours.
3. Include spread, commission, swap and slippage in tests.
4. Use chronological train/validation/out-of-sample splits and walk-forward checks.
5. Confirm API base URL, request signing and account permissions.
6. Validate order acknowledgement, fills, rejects, reconnect recovery and duplicate prevention on the broker demo account.
7. Set hard risk limits and require explicit approval before any live deployment.

Never commit API keys, private keys, passwords or account secrets.

## Exness configuration (read-only)

The project can now read account settings and XAUUSD trading conditions from
the Exness Trader API without placing orders. Configure the API credentials,
account ID, and the account Access Point securely, then run `exness-config`.
See [the setup guide](docs/EXNESS_READ_ONLY_SETUP.md). Never commit or paste
private signing keys. This command is read-only and does not enable trading.
