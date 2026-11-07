#!/usr/bin/env python3
"""exness_all.py-র API ব্যবহার করে M5 ক্যান্ডেল ডাউনলোড করে CSV বানায়।

ব্যবহার (exness_all.py আর settings.json যে ফোল্ডারে আছে সেখানে):
    python download_m5.py              # M5, ১২ মাস
    python download_m5.py 12 H1        # H1 (১ ঘণ্টা), ১২ মাস
    python download_m5.py 6 M15        # M15, ৬ মাস
টাইমফ্রেম: M1, M5, M15, H1
আউটপুট: exness_<টাইমফ্রেম>_<N>m.csv  (time,open,high,low,close,volume,spread)
"""
import sys, time, csv, os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def download(api, candle_row, months, out, tf="M5", step_bars=900, pause=0.3):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start_limit = now - timedelta(days=int(months * 30.5))
    step = timedelta(minutes=TF_MIN[tf] * step_bars)
    rows, end, empty, n = {}, now, 0, 0
    while end > start_limit:
        begin = max(end - step, start_limit)
        got = None
        for attempt in range(3):
            try:
                got = api.candles(tf, begin, end)
                break
            except Exception as e:
                print(f"  ত্রুটি ({attempt + 1}/3): {str(e)[:120]}")
                time.sleep(2 * (attempt + 1))
        if got is None:
            print(f"  {begin:%Y-%m-%d} - {end:%Y-%m-%d} বাদ গেল")
        elif not got:
            empty += 1
            if empty >= 4:
                print("টানা ৪টা খালি অংশ, এর আগের ডেটা সম্ভবত নেই। থামছি।")
                break
        else:
            empty = 0
            for c in got:
                r = candle_row(c)
                rows[r[0]] = r
        n += 1
        if n % 5 == 0:
            print(f"{begin:%Y-%m-%d} পর্যন্ত এসেছে, মোট {len(rows)} বার")
        end = begin
        time.sleep(pause)
    keys = sorted(rows)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume", "spread"])
        for k in keys:
            t, o, h, l, c, v, sp = rows[k]
            w.writerow([t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), o, h, l, c, v, sp])
    return len(keys), (keys[0] if keys else None), (keys[-1] if keys else None)


def main():
    months = float(sys.argv[1]) if len(sys.argv) > 1 else 12
    tf = (sys.argv[2] if len(sys.argv) > 2 else "M5").upper()
    if tf not in TF_MIN:
        sys.exit("টাইমফ্রেম M1, M5, M15 বা H1 দাও")
    import exness_all as ex
    cfg = ex.CFG
    if not ex.configured(cfg):
        sys.exit("settings.json-এ API তথ্য নেই। আগে ড্যাশবোর্ডের Settings থেকে সেভ করো।")
    api = ex.Api(cfg)
    out = os.path.join(HERE, f"exness_{tf.lower()}_{int(months)}m.csv")
    print(f"{cfg['instrument']} {tf}, {months:g} মাস, ফাইল: {out}")
    n, a, b = download(api, ex.candle_row, months, out, tf)
    print(f"শেষ: {n} বার | {a} থেকে {b}")
    if n < (1000 if tf in ("M1", "M5") else 200):
        print("সতর্কতা: খুব কম ডেটা এসেছে, API হয়তো এত পুরোনো ডেটা দেয় না।")


if __name__ == "__main__":
    main()
