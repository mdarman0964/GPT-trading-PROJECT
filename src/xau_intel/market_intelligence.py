"""Causal, gap-aware XAUUSD market-intelligence features."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
REQUIRED = ("time", "open", "high", "low", "close", "volume", "spread")
@dataclass(frozen=True)
class IntelligenceConfig:
    timeframe_minutes: int = 1
    atr_period: int = 14
    swing_left: int = 3
    swing_right: int = 3
    liquidity_lookback: int = 240
    sweep_min_atr: float = 0.05
    displacement_body_atr: float = 0.80
    equal_level_tolerance_atr: float = 0.10
    def validate(self):
        if self.timeframe_minutes < 1 or self.atr_period < 2: raise ValueError("Invalid timeframe or ATR period")
        if self.swing_left < 1 or self.swing_right < 1: raise ValueError("Swing windows must be >= 1")
        if self.liquidity_lookback < self.swing_left + self.swing_right + 1: raise ValueError("liquidity_lookback is too short")
        if min(self.sweep_min_atr, self.displacement_body_atr, self.equal_level_tolerance_atr) < 0: raise ValueError("Thresholds cannot be negative")
def validate_market_data(df, timeframe_minutes=1):
    missing = sorted(set(REQUIRED).difference(df.columns))
    if missing: raise ValueError(f"Missing required columns: {missing}")
    out = df.loc[:, REQUIRED].copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    if out["time"].isna().any(): raise ValueError("Invalid timestamps found")
    for col in REQUIRED[1:]: out[col] = pd.to_numeric(out[col], errors="coerce")
    if not np.isfinite(out.loc[:, REQUIRED[1:]].to_numpy(dtype=float)).all(): raise ValueError("OHLCV/spread contains missing or non-finite values")
    if out["time"].duplicated().any(): raise ValueError("Duplicate timestamps found")
    out = out.sort_values("time").reset_index(drop=True)
    if (out[["open","high","low","close"]] <= 0).any().any(): raise ValueError("OHLC prices must be positive")
    invalid = ((out["low"] > out["high"]) | (out["open"] < out["low"]) | (out["open"] > out["high"]) | (out["close"] < out["low"]) | (out["close"] > out["high"]))
    if invalid.any(): raise ValueError(f"Invalid OHLC rows: {int(invalid.sum())}")
    if (out["volume"] < 0).any() or (out["spread"] < 0).any(): raise ValueError("Volume and spread cannot be negative")
    delta = out["time"].diff(); expected = pd.Timedelta(minutes=timeframe_minutes)
    out["segment_id"] = (delta.ne(expected) & delta.notna()).cumsum().astype("int64")
    out["gap_before"] = delta.notna() & delta.ne(expected)
    return out
def _seg_roll(series, segments, window, operation):
    return series.groupby(segments, sort=False).transform(lambda x: getattr(x.rolling(window, min_periods=window), operation)())
def build_market_intelligence(df, cfg=IntelligenceConfig()):
    """Causal swings, liquidity references, sweeps, displacement, FVG and sessions."""
    cfg.validate(); out = validate_market_data(df, cfg.timeframe_minutes); seg = out["segment_id"]
    prev_close = out["close"].groupby(seg, sort=False).shift(1)
    tr = pd.concat([out["high"]-out["low"], (out["high"]-prev_close).abs(), (out["low"]-prev_close).abs()], axis=1).max(axis=1)
    out["true_range"] = tr
    out["atr"] = tr.groupby(seg, sort=False).transform(lambda x: x.rolling(cfg.atr_period, min_periods=cfg.atr_period).mean())
    n=len(out); hi=out["high"].to_numpy(float); lo=out["low"].to_numpy(float); sid=seg.to_numpy()
    he=np.full(n,np.nan); le=np.full(n,np.nan); left=cfg.swing_left; right=cfg.swing_right
    for p in range(left,n-right):
        c=p+right
        if not np.all(sid[p-left:p+right+1] == sid[p]): continue
        if hi[p] >= hi[p-left:p].max() and hi[p] > hi[p+1:p+right+1].max(): he[c]=hi[p]
        if lo[p] <= lo[p-left:p].min() and lo[p] < lo[p+1:p+right+1].min(): le[c]=lo[p]
    out["confirmed_swing_high_event"]=he; out["confirmed_swing_low_event"]=le
    out["prior_swing_high"]=pd.Series(he,index=out.index).groupby(seg,sort=False).ffill()
    out["prior_swing_low"]=pd.Series(le,index=out.index).groupby(seg,sort=False).ffill()
    out["prior_range_high"]=_seg_roll(out["high"],seg,cfg.liquidity_lookback,"max").groupby(seg,sort=False).shift(1)
    out["prior_range_low"]=_seg_roll(out["low"],seg,cfg.liquidity_lookback,"min").groupby(seg,sort=False).shift(1)
    atr=out["atr"].where(out["atr"]>0); tol=atr*cfg.equal_level_tolerance_atr
    ph=out["high"].groupby(seg,sort=False).shift(1); pl=out["low"].groupby(seg,sort=False).shift(1)
    out["equal_high_touch"]=((ph-out["prior_range_high"]).abs()<=tol).fillna(False)
    out["equal_low_touch"]=((pl-out["prior_range_low"]).abs()<=tol).fillna(False)
    for col in ("equal_high_touch","equal_low_touch"):
        out[col.replace("_touch","_count")]=out[col].groupby(seg,sort=False).transform(lambda x:x.astype(int).rolling(cfg.liquidity_lookback,min_periods=1).sum())
    out["sweep_high"]=((out["high"]>out["prior_swing_high"]+cfg.sweep_min_atr*atr)&(out["close"]<out["prior_swing_high"])).fillna(False)
    out["sweep_low"]=((out["low"]<out["prior_swing_low"]-cfg.sweep_min_atr*atr)&(out["close"]>out["prior_swing_low"])).fillna(False)
    out["sweep_high_level"]=out["prior_swing_high"].where(out["sweep_high"]); out["sweep_low_level"]=out["prior_swing_low"].where(out["sweep_low"])
    out["sweep_high_depth_atr"]=((out["high"]-out["prior_swing_high"])/atr).where(out["sweep_high"])
    out["sweep_low_depth_atr"]=((out["prior_swing_low"]-out["low"])/atr).where(out["sweep_low"])
    out["body_atr"]=(out["close"]-out["open"]).abs()/atr
    out["displacement_up"]=((out["close"]>out["open"])&(out["body_atr"]>=cfg.displacement_body_atr)).fillna(False)
    out["displacement_down"]=((out["close"]<out["open"])&(out["body_atr"]>=cfg.displacement_body_atr)).fillna(False)
    h2=out["high"].groupby(seg,sort=False).shift(2); l2=out["low"].groupby(seg,sort=False).shift(2)
    out["bullish_fvg"]=(out["low"]>h2).fillna(False); out["bearish_fvg"]=(out["high"]<l2).fillna(False)
    out["bullish_fvg_size"]=(out["low"]-h2).where(out["bullish_fvg"]); out["bearish_fvg_size"]=(l2-out["high"]).where(out["bearish_fvg"])
    dubai=out["time"].dt.tz_localize("Asia/Dubai")
    lh=dubai.dt.tz_convert("Europe/London").dt.hour; nh=dubai.dt.tz_convert("America/New_York").dt.hour
    out["dubai_hour"]=dubai.dt.hour; out["london_hour"]=lh; out["new_york_hour"]=nh
    out["london_session"]=(lh>=8)&(lh<17); out["new_york_session"]=(nh>=8)&(nh<17)
    out["london_new_york_overlap"]=out["london_session"]&out["new_york_session"]
    return out
