"""USD account-level simulation layered on top of the existing price backtest.

Contract size and leverage are mandatory inputs because they vary by broker
and account. This is a single-position portfolio simulation: overlapping
candidate trades are skipped in chronological order, not added together.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import pandas as pd


@dataclass(frozen=True)
class AccountConfig:
    initial_balance: float = 1000.0
    risk_fraction: float = 0.01
    contract_size: float = 100.0
    leverage: float = 100.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0
    commission_usd_per_lot_round_turn: float = 0.0

    def validate(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0 < self.risk_fraction <= 0.05:
            raise ValueError("risk_fraction must be in (0, 0.05]")
        for name in ("contract_size", "leverage", "min_lot", "lot_step", "max_lot"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_lot > self.max_lot:
            raise ValueError("min_lot cannot exceed max_lot")
        if self.commission_usd_per_lot_round_turn < 0:
            raise ValueError("commission cannot be negative")


def _floor_step(value: float, step: float) -> float:
    return math.floor((value + 1e-12) / step) * step


def run_account_backtest(trades: pd.DataFrame, cfg: AccountConfig = AccountConfig()
                         ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Size each non-overlapping trade to account risk and calculate USD equity."""
    cfg.validate()
    required = {"entry_time", "exit_time", "entry_index", "exit_index",
                "entry_mid", "pnl_price", "stop_distance", "entry_spread",
                "exit_spread", "hypothesis"}
    missing = sorted(required.difference(trades.columns))
    if missing and not trades.empty:
        raise ValueError(f"Trade data missing columns: {missing}")

    balance = cfg.initial_balance
    peak = balance
    last_exit_index = -1
    rows: list[dict] = []
    equity_rows = [{"time": None, "balance": balance, "equity": balance,
                    "drawdown_usd": 0.0, "drawdown_pct": 0.0}]
    skipped_overlap = skipped_margin = skipped_min_lot = 0

    ordered = trades.sort_values(["entry_index", "exit_index"]).reset_index(drop=True) if not trades.empty else trades
    for _, t in ordered.iterrows():
        entry_idx, exit_idx = int(t.entry_index), int(t.exit_index)
        if entry_idx <= last_exit_index:
            skipped_overlap += 1
            continue

        entry_cost = float(t.entry_spread) / 2.0
        exit_cost = float(t.exit_spread) / 2.0
        risk_per_unit_price = (float(t.stop_distance) + entry_cost + exit_cost)
        if risk_per_unit_price <= 0:
            continue
        commission = cfg.commission_usd_per_lot_round_turn
        risk_budget = balance * cfg.risk_fraction
        risk_per_lot = risk_per_unit_price * cfg.contract_size + commission
        raw_lots = min(cfg.max_lot, risk_budget / risk_per_lot)
        lots = _floor_step(raw_lots, cfg.lot_step)
        if lots < cfg.min_lot:
            skipped_min_lot += 1
            continue

        margin_per_lot = float(t.entry_mid) * cfg.contract_size / cfg.leverage
        free_margin = balance
        affordable = _floor_step(free_margin / margin_per_lot, cfg.lot_step) if margin_per_lot > 0 else 0
        if affordable < cfg.min_lot:
            skipped_margin += 1
            continue
        lots = min(lots, affordable)
        if lots < cfg.min_lot:
            skipped_margin += 1
            continue

        gross_and_price_cost_pnl = float(t.pnl_price) * cfg.contract_size * lots
        commission_usd = commission * lots
        pnl_usd = gross_and_price_cost_pnl - commission_usd
        start_balance = balance
        balance += pnl_usd
        peak = max(peak, balance)
        dd = balance - peak
        dd_pct = (dd / peak * 100.0) if peak > 0 else -100.0
        rows.append({
            **t.to_dict(), "lots": lots, "risk_budget_usd": risk_budget,
            "estimated_margin_usd": margin_per_lot * lots,
            "pnl_usd": pnl_usd, "commission_usd": commission_usd,
            "balance_before": start_balance, "balance_after": balance,
            "drawdown_usd": dd, "drawdown_pct": dd_pct,
        })
        equity_rows.append({"time": t.exit_time, "balance": balance, "equity": balance,
                            "drawdown_usd": dd, "drawdown_pct": dd_pct})
        last_exit_index = exit_idx
        if balance <= 0:
            break

    result = pd.DataFrame(rows)
    equity = pd.DataFrame(equity_rows)
    pnl = result["pnl_usd"] if not result.empty else pd.Series(dtype=float)
    gross_profit = float(pnl[pnl > 0].sum()) if not pnl.empty else 0.0
    gross_loss = float(-pnl[pnl < 0].sum()) if not pnl.empty else 0.0
    report = {
        "config": asdict(cfg),
        "execution_model": "Historical account simulation; no broker orders",
        "portfolio_rule": "One position globally; overlapping candidate trades skipped in entry-index order",
        "sizing_rule": "Risk fraction of current balance; lots rounded down to lot_step; margin capped by available balance",
        "trades_taken": int(len(result)),
        "skipped_overlapping": skipped_overlap,
        "skipped_below_min_lot": skipped_min_lot,
        "skipped_for_margin": skipped_margin,
        "initial_balance_usd": cfg.initial_balance,
        "ending_balance_usd": float(balance),
        "net_profit_usd": float(balance - cfg.initial_balance),
        "return_pct": float((balance / cfg.initial_balance - 1.0) * 100.0),
        "win_rate": float((pnl > 0).mean()) if not pnl.empty else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else "inf"),
        "max_drawdown_usd": float(equity["drawdown_usd"].min()),
        "max_drawdown_pct": float(equity["drawdown_pct"].min()),
        "account_depleted": bool(balance <= 0),
        "limitations": [
            "Contract size and leverage must be checked against the exact Exness account/instrument specification.",
            "Margin is approximated as lots * contract_size * entry price / leverage; broker-specific margin rules and stop-out are not modeled.",
            "Swap, financing, currency conversion, partial fills, latency, slippage beyond the base backtest, and changing margin rates are not modeled.",
            "Overlapping trades are skipped; this is not a multi-position margin/netting engine.",
        ],
    }
    return result, equity, report
