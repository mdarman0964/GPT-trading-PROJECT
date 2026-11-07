import argparse, json
from .research import load_candles, audit, add_features
from .hypotheses import build_hypotheses

def main():
    p = argparse.ArgumentParser(description="XAUUSD integrated research tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit"); a.add_argument("--csv", required=True)
    f = sub.add_parser("features"); f.add_argument("--csv", required=True); f.add_argument("--out", required=True)
    h = sub.add_parser("hypotheses"); h.add_argument("--csv", required=True); h.add_argument("--out", required=True)
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

if __name__ == "__main__":
    main()
