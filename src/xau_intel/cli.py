import argparse, json
from .research import load_candles, audit, add_features
from .hypotheses import build_hypotheses
from .backtest import BacktestConfig, run_backtest, write_report

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
