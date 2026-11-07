import argparse, json
from .research import load_candles, audit, add_features

def main():
    p = argparse.ArgumentParser(description="XAUUSD offline research tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit"); a.add_argument("--csv", required=True)
    f = sub.add_parser("features"); f.add_argument("--csv", required=True); f.add_argument("--out", required=True)
    args = p.parse_args(); df = load_candles(args.csv)
    if args.cmd == "audit": print(json.dumps(audit(df), indent=2))
    elif args.cmd == "features":
        add_features(df).to_csv(args.out, index=False)
        print(f"Wrote {len(df)} rows to {args.out}")

if __name__ == "__main__": main()
