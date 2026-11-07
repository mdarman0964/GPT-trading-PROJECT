# Exness read-only account configuration

The project now includes a **read-only** Exness Trader API configuration reader.
It retrieves the account settings and the XAUUSD instrument conditions needed
before a USD account simulation can be parameterized.

## Required environment

Set these in the runtime's secret manager or local environment (never commit
them, paste them into chat, or add them to screenshots):

- `EXN_API_KEY`: API key identifier from Exness Key Management.
- `EXN_PRIVATE_KEY_FILE`: path to an unencrypted Ed25519 PKCS#8 PEM file; or
  `EXN_PRIVATE_KEY`: Exness-generated 128-hex-character private key value.
- `EXN_ACCOUNT_ID`: numeric trading account ID.
- `EXN_API_BASE_URL`: HTTPS account Access Point returned by Exness host
  discovery. Do not guess this host or use a static example hostname.

## Run

After installing the project dependencies:

```bash
exness-config
```

The command writes `output/exness_xauusd_config.json` and prints a summary.
The response includes account currency/leverage/status, XAUUSD contract size,
volume min/max/step, swap values, commission settings, and margin percent.

## Safety boundary

This command only performs signed GET requests. It cannot place, change, or
close orders. It does not discover or guess the account Access Point; obtain
that from the Exness Key Management DNS widget / approved host-discovery flow.
Commission charging convention, HMR, and account-specific margin rules must
be reviewed before using the values in an account backtest. No live or demo
trading is enabled by this reader.
