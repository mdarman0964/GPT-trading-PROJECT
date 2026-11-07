"""Causal XAUUSD hypothesis features.

These are research labels, not a live trading strategy. Every level used for a
bar is shifted so the current candle cannot leak into its own reference level.
Signals are evaluated only after the candle closes.
"""
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class HypothesisConfig:
    atr_period: int = 14
    level_lookback: int = 20
    trend_fast: int = 20
    trend_slow: int = 50
    mean_period: int = 50
    sweep_min_atr: float = 0.05
    breakout_buffer_atr: float = 0.02
    max_spread_to_atr: float = 0.20


def build_hypotheses(df: pd.DataFrame, cfg: HypothesisConfig = HypothesisConfig()) -> pd.DataFrame:
    """Return source candles plus causal features and independent candidate events.

    Required columns: time, open, high, low, close, spread.
    No entries, exits, position sizing, or profitability claims are produced.
    """
    required = {"time", "open", "high", "low", "close", "spread"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if min(cfg.atr_period, cfg.level_lookback, cfg.trend_fast,
           cfg.trend_slow, cfg.mean_period) < 2:
        raise ValueError("All lookback periods must be >= 2")
    if cfg.trend_fast >= cfg.trend_slow:
        raise ValueError("trend_fast must be smaller than trend_slow")

    out = df.copy().sort_values("time").reset_index(drop=True)
    if out["time"].duplicated().any():
        raise ValueError("Duplicate timestamps: resolve data before building hypotheses")

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [out["high"] - out["low"],
         (out["high"] - prev_close).abs(),
         (out["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(cfg.atr_period, min_periods=cfg.atr_period).mean()

    # Prior completed-candle levels: shift(1) prevents current-bar leakage.
    prior_high = out["high"].rolling(cfg.level_lookback).max().shift(1)
    prior_low = out["low"].rolling(cfg.level_lookback).min().shift(1)
    out["prior_range_high"] = prior_high
    out["prior_range_low"] = prior_low

    # Liquidity sweep / reclaim candidates.
    out["sweep_long"] = (
        (out["low"] < prior_low - cfg.sweep_min_atr * out["atr"])
        & (out["close"] > prior_low)
    ).fillna(False)
    out["sweep_short"] = (
        (out["high"] > prior_high + cfg.sweep_min_atr * out["atr"])
        & (out["close"] < prior_high)
    ).fillna(False)

    # Close-confirmed breakout candidates, distinct from sweep/reclaim events.
    out["breakout_long"] = (
        out["close"] > prior_high + cfg.breakout_buffer_atr * out["atr"]
    ).fillna(False)
    out["breakout_short"] = (
        out["close"] < prior_low - cfg.breakout_buffer_atr * out["atr"]
    ).fillna(False)

    out["ema_fast"] = out["close"].ewm(span=cfg.trend_fast, adjust=False,
                                         min_periods=cfg.trend_fast).mean()
    out["ema_slow"] = out["close"].ewm(span=cfg.trend_slow, adjust=False,
                                         min_periods=cfg.trend_slow).mean()
    out["trend_up"] = (out["ema_fast"] > out["ema_slow"]).fillna(False)
    out["trend_down"] = (out["ema_fast"] < out["ema_slow"]).fillna(False)

    out["mean"] = out["close"].rolling(cfg.mean_period,
                                         min_periods=cfg.mean_period).mean()
    out["mean_z_atr"] = (out["close"] - out["mean"]) / out["atr"].where(out["atr"] > 0)
    out["mean_revert_long"] = (out["mean_z_atr"] <= -1.5).fillna(False)
    out["mean_revert_short"] = (out["mean_z_atr"] >= 1.5).fillna(False)

    out["spread_to_atr"] = out["spread"] / out["atr"].where(out["atr"] > 0)
    out["spread_ok"] = (out["spread_to_atr"] <= cfg.max_spread_to_atr).fillna(False)
    out["hour"] = pd.to_datetime(out["time"]).dt.hour

    # Keep candidates independent. Combining them requires out-of-sample evidence.
    return out
