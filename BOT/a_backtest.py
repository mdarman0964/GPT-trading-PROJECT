#!/usr/bin/env python3
"""A (Volume Profile) ইতিহাসে পরীক্ষা: SMA-সহ আর SMA-ছাড়া তুলনা। বটের কিছুই বদলায় না, শুধু পড়ে।

ব্যবহার (exness_all.py-র ফোল্ডারে):
    python a_backtest.py                  # settings.json-এর টাইমফ্রেম, শেষ ৩০ দিন
    python a_backtest.py --days 60 --tf M1
    python a_backtest.py --csv hist.csv   # নিজের CSV দিলে API লাগে না (time,open,high,low,close,volume,spread)

সেটিংস (SL, tol, lookback, bins, SMA, BE, TP...) settings.json থেকে নেয়। সিগন্যালের হিসাব বটের নিজের profile() ফাংশনেই।
ধরন: এক সময়ে একটা ট্রেড, এন্ট্রি বারের শুরুতে (বট 'bar' মোডে যেমন), বারের ভেতরে আগে SL ছুঁল ধরা হয় (নিরাশাবাদী)।
"""
import argparse, csv, os, sys, time
from datetime import datetime, timedelta, timezone
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exness_all as ex

ap = argparse.ArgumentParser()
ap.add_argument("--tf", default=None)
ap.add_argument("--days", type=int, default=30)
ap.add_argument("--csv", default=None)
args = ap.parse_args()
cfg = dict(ex.CFG)
tf = args.tf or cfg["tf"]
tfm = ex.TF_MIN[tf]


def load_csv(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            t = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            rows.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                         float(r.get("volume") or 0), float(r["spread"])))
    return rows


def download(days):
    """ধীরে ধীরে নামায় (rate limit মেনে), নতুন থেকে পুরনোর দিকে। বারবার সেভ করে, থেমে গেলে আবার চালালে যেখানে থেমেছিল সেখান থেকে চলে।"""
    api = ex.Api(cfg)
    path = os.path.join(HERE, f"hist_{api.inst}_{tf}_a.csv")
    have = {}
    if os.path.exists(path):
        for r in load_csv(path):
            have[r[0]] = r
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=days)
    step = timedelta(minutes=tfm * 900)
    pad = timedelta(minutes=3 * tfm)
    DELAY = 1.1          # প্রতি অনুরোধের মাঝে বিরতি (সেকেন্ড)

    def save():
        keep = sorted(k for k in have if k >= start and k.timestamp() + tfm * 60 <= now.timestamp())
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("time,open,high,low,close,volume,spread\n")
            for k in keep:
                r = have[k]; f.write(f"{k:%Y-%m-%d %H:%M:%S},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[6]}\n")
        os.replace(tmp, path)
        return keep

    def fetch(a, b):
        wait = 5
        for _ in range(12):
            try:
                out = api.candles(tf, a, b)
                time.sleep(DELAY)
                return out
            except (RuntimeError, OSError) as e:
                m = str(e) if isinstance(e, RuntimeError) else "timed out"
                if any(x in m for x in ("429", "RATE_LIMIT", "503", "নেটওয়ার্ক", "timed out", "Temporary")):
                    print(f"\nAPI ব্যস্ত বা সংযোগ সমস্যা, {wait} সেকেন্ড অপেক্ষা করে আবার চেষ্টা...", flush=True)
                    time.sleep(wait); wait = min(wait * 2, 90)
                    continue
                raise
        raise RuntimeError("বারবার rate limit, পরে আবার চালাও (এ পর্যন্ত নামানো অংশ সেভ আছে)")

    def pull(a, b):
        got = 0
        for c in fetch(a, b):
            r = ex.candle_row(c); have[r[0]] = r; got += 1
        return got

    try:
        if have:                                   # আগে নতুন অংশ
            pull(max(have) - pad, now)
        end = min(have) + pad if have else now     # তারপর পুরনোর দিকে ধাপে ধাপে
        n_chunks = 0; empty = 0
        while end > start + pad and not (have and min(have) <= start + pad):
            a = max(start, end - step)
            got = pull(a, end)
            n_chunks += 1
            empty = empty + 1 if got == 0 else 0
            print(f"\rডাউনলোড {a:%Y-%m-%d %H:%M} পর্যন্ত ({len(have)} বার)", end="", flush=True)
            if n_chunks % 8 == 0:
                save()
            if empty >= 3:
                print("\nAPI আর পুরনো ডেটা দিচ্ছে না, এখানেই থামলাম।")
                break
            end = a
    finally:
        keep = save()
    print()
    print(f"{len(keep)} বার সেভ: {path}")
    if keep and keep[0] > start + timedelta(days=2):
        print(f"সতর্কতা: {days} দিনের ডেটা পাওয়া যায়নি, সবচেয়ে পুরনো বার {keep[0]:%Y-%m-%d}")
    return [have[k] for k in keep]


rows = load_csv(args.csv) if args.csv else download(args.days)
if len(rows) < int(cfg["lookback"]) + int(cfg["sma"]) + 50:
    sys.exit("ডেটা কম")
O = np.array([r[1] for r in rows]); H = np.array([r[2] for r in rows]); L = np.array([r[3] for r in rows])
C = np.array([r[4] for r in rows]); V = np.array([r[5] for r in rows], dtype=float); SP = np.array([r[6] for r in rows])
N = len(rows)
lb, bins, smap = int(cfg["lookback"]), int(cfg["bins"]), int(cfg["sma"])
tol = float(cfg["tol"]); max_sp = float(cfg["max_spread"]); dirs = cfg["dirs"]
tick = cfg.get("entry", "bar") == "tick"
SL = float(cfg["sl"]); contract = float(cfg["contract"])
be_on = cfg.get("be_on") == "on"; be_pct = float(cfg["be_pct"]); be_buf = float(cfg["be_buf"])


def signal_at(i, use_sma):
    """বটের signal_on_last_bar-এর একই হিসাব; use_sma=False হলে ট্রেন্ডের শর্ত বাদ।"""
    sp = SP[i]
    if max_sp and sp > max_sp:
        return None
    p = ex.profile(H, L, V, i, lb, bins)
    if p is None:
        return None
    poc, val, vah = p
    cur = C[i] if tick else O[i]
    price = cur + sp / 2
    near_val = abs(price - val) <= tol
    near_poc = abs(price - poc) <= tol
    near_vah = abs(price - vah) <= tol
    buy_c = near_val or (near_poc and price >= poc)
    sell_c = near_vah or (near_poc and price <= poc)
    if use_sma:
        sma = (C[i - smap + 1:i].sum() + cur) / smap
        buy = price > sma and buy_c
        sell = (not buy) and price < sma and sell_c
    else:
        buy = buy_c
        sell = (not buy) and sell_c
    if dirs == "S":
        buy = False
    if dirs == "L":
        sell = False
    sig = "buy" if buy else "sell" if sell else None
    return sig, {"poc": float(poc), "val": float(val), "vah": float(vah)}


def run(use_sma):
    trades = []
    i = lb + smap + 2
    while i < N:
        s = signal_at(i, use_sma)
        if not s or not s[0]:
            i += 1
            continue
        sig, lv = s
        ex.LEVELS = dict(lv)
        sp = SP[i]; bid = C[i] if tick else O[i]; ask = bid + sp
        if sig == "sell":
            slp, ref = bid + SL + sp, bid
        else:
            slp, ref = ask - SL - sp, ask
        tp, _ = ex.pick_tp(sig, ref, cfg)
        if tp is None:
            i += 1
            continue
        sg = 1 if sig == "buy" else -1
        D = abs(tp - ref); sl = slp; moved = False; ex_p = None; j = i
        dist0 = abs(ref - slp)
        while j < N:
            spj = SP[j]
            lo_b, hi_b = L[j], H[j]
            if sg == 1:
                worst, best = lo_b, hi_b
            else:
                worst, best = hi_b + spj, lo_b + spj
            if sg * (worst - sl) <= 0:
                ex_p = sl; break
            if sg * (tp - best) <= 0:
                ex_p = tp; break
            if be_on and not moved and D > 0 and sg * (best - ref) / D >= be_pct / 100:
                nsl = ref + sg * be_buf
                if sg * (nsl - sl) > 0:
                    sl = nsl; moved = True
            j += 1
        if ex_p is None:
            break
        pnl_pts = sg * (ex_p - ref)
        trades.append({"t": rows[i][0], "side": sig, "R": pnl_pts / dist0, "usd": pnl_pts * 0.01 * contract,
                       "tpd": D, "bars": j - i})
        i = j + 1
    return trades


def stats(tr):
    n = len(tr)
    if not n:
        return None
    rs = [t["R"] for t in tr]
    eq = pk = dd = 0.0
    for r in rs:
        eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dict(n=n, win=100 * sum(1 for r in rs if r > 0) / n, avg=sum(rs) / n, tot=sum(rs), dd=dd,
                usd=sum(t["usd"] for t in tr))


def show(label, tr):
    s = stats(tr)
    if not s:
        print(f"{label}: কোনো ট্রেড নেই"); return
    print(f"{label:14} ট্রেড {s['n']:4} | জিত {s['win']:3.0f}% | গড় {s['avg']:+.2f}R | মোট {s['tot']:+7.1f}R | "
          f"সর্বোচ্চ ড্রডাউন {s['dd']:5.1f}R | ০.০১ লটে {s['usd']:+8.2f}$")


print(f"\nডেটা: {rows[0][0]:%Y-%m-%d} → {rows[-1][0]:%Y-%m-%d} ({N} বার, {tf}) | SL ${SL:g}, tol {tol:g}, lookback {lb}, SMA {smap}, "
      f"BE {'চালু ' + format(be_pct, 'g') + '%' if be_on else 'বন্ধ'}, dirs {dirs}")
t0 = time.time()
with_sma = run(True); no_sma = run(False)
print(f"(হিসাব {time.time() - t0:.0f} সেকেন্ড)\n")
for label, tr in (("SMA-সহ (এখনকার)", with_sma), ("SMA ছাড়া", no_sma)):
    show(label, tr)
    for sd in ("buy", "sell"):
        show("   " + sd, [t for t in tr if t["side"] == sd])
extra = [t for t in no_sma if t["t"] not in {x["t"] for x in with_sma}]
print()
show("শুধু SMA-ছাড়ায় যোগ হওয়া", extra)
print("\nসতর্কতা: এক সময়ে একটা ট্রেড, বারের ভেতরে আগে SL ধরা হয়, কমিশন/স্লিপেজ নেই। ফল ইঙ্গিত মাত্র, ট্রেড কম হলে ভরসা কম।")
