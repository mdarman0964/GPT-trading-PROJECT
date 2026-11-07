from __future__ import annotations
import pandas as pd
import numpy as np

REQUIRED = ["time", "open", "high", "low", "close", "volume", "spread"]

def load_candles(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df = df[REQUIRED].copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    for c in REQUIRED[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df.isna().any().any():
        raise ValueError("CSV contains invalid or missing values; inspect before analysis.")
    return df.sort_values("time").reset_index(drop=True)

def audit(df: pd.DataFrame) -> dict:
    t = df["time"]
    invalid_ohlc = ((df["low"] > df["high"]) | (df["open"] > df["high"]) | (df["open"] < df["low"]) | (df["close"] > df["high"]) | (df["close"] < df["low"]))
    gaps = t.diff().dropna()
    return {
        "rows": int(len(df)), "start": str(t.min()), "end": str(t.max()),
        "duplicate_timestamps": int(t.duplicated().sum()),
        "missing_values": {k: int(v) for k, v in df.isna().sum().items()},
        "invalid_ohlc_rows": int(invalid_ohlc.sum()),
        "negative_spread_rows": int((df["spread"] < 0).sum()),
        "gaps_over_1m": int((gaps > pd.Timedelta(minutes=1)).sum()),
        "median_spread": float(df["spread"].median()),
        "max_spread": float(df["spread"].max()),
        "timezone": "unspecified; timestamps interpreted as naive source timestamps"
    }

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat([out["high"] - out["low"], (out["high"] - prev_close).abs(), (out["low"] - prev_close).abs()], axis=1).max(axis=1)
    out["return_1"] = out["close"].pct_change()
    out["log_return_1"] = np.log(out["close"]).diff()
    out["true_range"] = tr
    out["atr_14"] = tr.rolling(14, min_periods=14).mean()
    out["atr_50"] = tr.rolling(50, min_periods=50).mean()
    out["rolling_high_20"] = out["high"].rolling(20, min_periods=20).max()
    out["rolling_low_20"] = out["low"].rolling(20, min_periods=20).min()
    out["rolling_high_200"] = out["high"].rolling(200, min_periods=200).max()
    out["rolling_low_200"] = out["low"].rolling(200, min_periods=200).min()
    out["spread_to_atr14"] = out["spread"] / out["atr_14"].replace(0, np.nan)
    return out
