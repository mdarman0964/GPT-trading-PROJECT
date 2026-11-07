"""Conservative bar-based hypothesis backtester.

Important assumptions:
- Signals are known only after their candle closes; entries occur at next bar open.
- CSV spread is assumed to be an absolute XAUUSD price amount (e.g. 0.24),
  not points. Confirm this against the broker's instrument specification.
- Spread is charged half on each side. Slippage and commission are optional
  price-unit equivalents per side; defaults are zero and must be configured.
- If stop and target are both touched in one candle, stop is assumed first.
- One open trade per hypothesis stream; no pyramiding or overlapping trades.
This is research simulation only; it does not send broker orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import pandas as pd

from .hypotheses import build_hypotheses


@dataclass(frozen=True)
class BacktestConfig:
    stop_atr: float = 1.0
    target_r: float = 1.5
    max_hold_bars: int = 60
    slippage_per_side: float = 0.0
    commission_per_side: float = 0.0
    require_spread_filter: bool = True

    def validate(self) -> None:
        if self.stop_atr <= 0 or self.target_r <= 0:
            raise ValueError("stop_atr and target_r must be positive")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars must be >= 1")
        if self.slippage_per_side < 0 or self.commission_per_side < 0:
            raise ValueError("Costs cannot be negative")


SIGNALS = {
    "sweep_long": 1, "sweep_short": -1,
    "breakout_long": 1, "breakout_short": -1,
    "mean_revert_long": 1, "mean_revert_short": -1,
}


def _simulate_stream(df: pd.DataFrame, signal_col: str, side: int,
                     cfg: BacktestConfig) -> list[dict]:
    """Numpy-backed equivalent of the bar simulator; avoids millions of DataFrame iloc calls."""
    trades: list[dict] = []
    signals = df[signal_col].to_numpy(dtype=bool)
    spread_ok = df["spread_ok"].to_numpy(dtype=bool)
    atr = df["atr"].to_numpy(dtype=float)
    spread = df["spread"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    times = df["time"].astype(str).to_numpy()
    spread_ratio = df["spread_to_atr"].to_numpy(dtype=float)
    n = len(df)
    i = 0

    while i < n - 1:
        if not signals[i] or (cfg.require_spread_filter and not spread_ok[i]):
            i += 1
            continue
        signal_atr = atr[i]
        if not pd.notna(signal_atr) or signal_atr <= 0:
            i += 1
            continue

        entry_i = i + 1
        entry_mid = float(open_[entry_i])
        entry_spread = float(spread[entry_i])
        half_entry_cost = entry_spread / 2.0 + cfg.slippage_per_side + cfg.commission_per_side
        distance = cfg.stop_atr * signal_atr
        if side == 1:
            entry_fill = entry_mid + half_entry_cost
            stop_level = entry_mid - distance
            target_level = entry_mid + cfg.target_r * distance
        else:
            entry_fill = entry_mid - half_entry_cost
            stop_level = entry_mid + distance
            target_level = entry_mid - cfg.target_r * distance

        last_i = min(n - 1, entry_i + cfg.max_hold_bars - 1)
        exit_i = last_i
        exit_reason = "time"
        exit_mid = float(close[last_i])

        for j in range(entry_i, last_i + 1):
            o, h, l = float(open_[j]), float(high[j]), float(low[j])
            if side == 1:
                if o <= stop_level:
                    exit_i, exit_mid, exit_reason = j, o, "stop_gap"
                    break
                if o >= target_level:
                    exit_i, exit_mid, exit_reason = j, o, "target_gap"
                    break
                if l <= stop_level:
                    exit_i, exit_mid, exit_reason = j, stop_level, "stop"
                    break
                if h >= target_level:
                    exit_i, exit_mid, exit_reason = j, target_level, "target"
                    break
            else:
                if o >= stop_level:
                    exit_i, exit_mid, exit_reason = j, o, "stop_gap"
                    break
                if o <= target_level:
                    exit_i, exit_mid, exit_reason = j, o, "target_gap"
                    break
                if h >= stop_level:
                    exit_i, exit_mid, exit_reason = j, stop_level, "stop"
                    break
                if l <= target_level:
                    exit_i, exit_mid, exit_reason = j, target_level, "target"
                    break

        exit_spread = float(spread[exit_i])
        half_exit_cost = exit_spread / 2.0 + cfg.slippage_per_side + cfg.commission_per_side
        exit_fill = (exit_mid - half_exit_cost) if side == 1 else (exit_mid + half_exit_cost)
        pnl = (exit_fill - entry_fill) * side
        risk_distance = distance + half_entry_cost + half_exit_cost
        trades.append({
            "hypothesis": signal_col, "side": "long" if side == 1 else "short",
            "signal_time": times[i], "entry_time": times[entry_i],
            "exit_time": times[exit_i],
            "entry_index": entry_i, "exit_index": exit_i,
            "entry_mid": entry_mid, "entry_fill": entry_fill,
            "exit_mid": exit_mid, "exit_fill": exit_fill,
            "entry_spread": entry_spread, "exit_spread": exit_spread,
            "atr_at_signal": float(signal_atr), "stop_distance": distance,
            "risk_distance": risk_distance, "exit_reason": exit_reason,
            "pnl_price": pnl,
            "r_multiple": pnl / risk_distance if risk_distance > 0 else 0.0,
            "spread_to_atr_at_signal": float(spread_ratio[i]),
            "bars_held": exit_i - entry_i + 1,
        })
        i = exit_i + 1
    return trades

def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "win_rate": 0.0, "net_pnl_price": 0.0,
                "profit_factor": None, "expectancy_r": None, "max_drawdown_r": 0.0}
    pnl = trades["pnl_price"].astype(float)
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    equity_r = trades["r_multiple"].astype(float).cumsum()
    peak = equity_r.cummax().clip(lower=0)
    drawdown = equity_r - peak
    pf = float(wins / losses) if losses > 0 else (None if wins == 0 else "inf")
    return {
        "trades": int(len(trades)),
        "win_rate": float((pnl > 0).mean()),
        "net_pnl_price": float(pnl.sum()),
        "gross_winning_price": float(wins),
        "gross_losing_price": float(losses),
        "profit_factor": pf,
        "expectancy_r": float(trades["r_multiple"].mean()),
        "max_drawdown_r": float(drawdown.min()),
        "average_r": float(trades["r_multiple"].mean()),
    }


def run_backtest(df: pd.DataFrame, cfg: BacktestConfig = BacktestConfig()) -> tuple[pd.DataFrame, dict]:
    cfg.validate()
    data = build_hypotheses(df)
    all_trades: list[dict] = []
    for signal_col, side in SIGNALS.items():
        all_trades.extend(_simulate_stream(data, signal_col, side, cfg))
    trades = pd.DataFrame(all_trades)
    summaries = {}
    for name in SIGNALS:
        subset = trades[trades["hypothesis"] == name] if not trades.empty else trades
        summaries[name] = summarize(subset)
    summaries["combined_all_candidates"] = summarize(trades)
    report = {
        "config": asdict(cfg),
        "assumptions": {
            "spread_unit": "Assumed absolute price units; verify broker specification",
            "signal_timing": "Signal candle close; entry next candle open",
            "same_bar_stop_target": "Stop first",
            "overlap": "One position per hypothesis stream; streams are independent",
            "execution": "Historical simulation only; no broker orders",
        },
        "by_hypothesis": summaries,
    }
    return trades, report


def write_report(trades: pd.DataFrame, report: dict, trades_path: str, report_path: str) -> None:
    Path(trades_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(trades_path, index=False)
    Path(report_path).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
