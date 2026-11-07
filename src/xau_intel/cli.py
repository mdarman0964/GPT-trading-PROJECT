import argparse, json
from .research import load_candles, audit, add_features
from .hypotheses import build_hypotheses
from .backtest import BacktestConfig, run_backtest, write_report
from .account import AccountConfig, run_account_backtest

def main():
    p = argparse.ArgumentParser(description="XAUUSD integrated research tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit"); a.add_argument("--csv", required=True)
    f = sub.add_parser("features"); f.add_argument("--csv", required=True); f.add_argument("--out", required=True)
    h = sub.add_parser("hypotheses"); h.add_argument("--csv", required=True); h.add_argument("--out", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--csv", required=True)
    b.add_argument("--trades-out", default="output/trades.csv")
    b.add_argument("--report-out", default="output/backtest_report.json")
    b.add_argument("--stop-atr", type=float, default=1.0)
    b.add_argument("--target-r", type=float, default=1.5)
    b.add_argument("--max-hold-bars", type=int, default=60)
    b.add_argument("--slippage-per-side", type=float, default=0.0,
                   help="Slippage in XAUUSD price units per side; configure from measured broker data")
    b.add_argument("--commission-per-side", type=float, default=0.0,
                   help="Commission converted to XAUUSD price units per side")
    b.add_argument("--allow-wide-spread", action="store_true",
                   help="Disable default spread/ATR filter")
    ab = sub.add_parser("account-backtest", help="Run dollar-denominated account simulation")
    ab.add_argument("--csv", required=True)
    ab.add_argument("--initial-balance", type=float, default=1000.0)
    ab.add_argument("--risk-percent", type=float, default=1.0)
    ab.add_argument("--contract-size", type=float, required=True)
    ab.add_argument("--leverage", type=float, required=True)
    ab.add_argument("--min-lot", type=float, default=0.01)
    ab.add_argument("--lot-step", type=float, default=0.01)
    ab.add_argument("--max-lot", type=float, default=100.0)
    ab.add_argument("--commission-usd-per-lot-round-turn", type=float, default=0.0)
    ab.add_argument("--slippage-per-side", type=float, default=0.0)
    ab.add_argument("--commission-per-side", type=float, default=0.0)
    ab.add_argument("--trades-out", default="output/account_trades.csv")
    ab.add_argument("--equity-out", default="output/equity_curve.csv")
    ab.add_argument("--report-out", default="output/account_report.json")
    args = p.parse_args()
    df = load_candles(args.csv)
    if args.cmd == "audit":
        print(json.dumps(audit(df), indent=2))
    elif args.cmd == "features":
        add_features(df).to_csv(args.out, index=False)
        print(f"Wrote {len(df)} feature rows to {args.out}")
    elif args.cmd == "hypotheses":
        result = build_hypotheses(df)
        result.to_csv(args.out, index=False)
        cols = ["sweep_long", "sweep_short", "breakout_long", "breakout_short",
                "mean_revert_long", "mean_revert_short"]
        counts = {c: int(result[c].sum()) for c in cols}
        print(json.dumps({"rows": len(result), "candidate_event_counts": counts,
                          "note": "Research candidates only; not a backtest or profitability result."},
                         indent=2))
    elif args.cmd == "account-backtest":
        base_cfg = BacktestConfig(slippage_per_side=args.slippage_per_side,
                                  commission_per_side=args.commission_per_side)
        raw_trades, _ = run_backtest(df, base_cfg)
        account_cfg = AccountConfig(
            initial_balance=args.initial_balance,
            risk_fraction=args.risk_percent / 100.0,
            contract_size=args.contract_size, leverage=args.leverage,
            min_lot=args.min_lot, lot_step=args.lot_step, max_lot=args.max_lot,
            commission_usd_per_lot_round_turn=args.commission_usd_per_lot_round_turn,
        )
        account_trades, equity, report = run_account_backtest(raw_trades, account_cfg)
        from pathlib import Path
        Path(args.trades_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.equity_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        account_trades.to_csv(args.trades_out, index=False)
        equity.to_csv(args.equity_out, index=False)
        Path(args.report_out).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(report, indent=2, allow_nan=False))
    elif args.cmd == "backtest":
        cfg = BacktestConfig(
            stop_atr=args.stop_atr, target_r=args.target_r,
            max_hold_bars=args.max_hold_bars,
            slippage_per_side=args.slippage_per_side,
            commission_per_side=args.commission_per_side,
            require_spread_filter=not args.allow_wide_spread,
        )
        trades, report = run_backtest(df, cfg)
        write_report(trades, report, args.trades_out, args.report_out)
        print(json.dumps(report, indent=2, allow_nan=False))

if __name__ == "__main__": main()
