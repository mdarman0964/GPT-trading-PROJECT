#!/usr/bin/env python3
"""
Exness সব-এক-ফাইল বট: ড্যাশবোর্ড + সিগন্যাল + অটো ট্রেড (শুধু ডেমো অ্যাকাউন্ট)

চালাও:   python exness_all.py      তারপর ব্রাউজারে http://localhost:8080
বন্ধ:     Ctrl+C
Termux:   pkg install python python-numpy python-cryptography

সব সেটিংস settings.json-এ সেভ হয় (প্রথমবার নিজে থেকে বানায়), Settings ট্যাব থেকেই বদলানো যায়।
ট্রেড মোড: বন্ধ (শুধু সিগন্যাল) / DRY-RUN (অর্ডার পাঠায় না) / লাইভ (অর্ডার পাঠায়)।
অ্যাকাউন্ট ডেমো না আসল, তা API-র is_demo থেকে নিজে ধরে: ডেমো কী দিলে ডেমোতে, আসল কী দিলে আসল অ্যাকাউন্টে চলে।
আসল অ্যাকাউন্টে লাইভ চালু করতে অ্যাকাউন্ট নম্বর লিখে নিশ্চিত করতে হয়, আর প্রতিবার প্রোগ্রাম চালুর পর আবার নিশ্চিত করতে হয়।
"""
import base64, csv, dataclasses, hashlib, json, os, sys, threading, time, uuid
import urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import strategy as FIB   # স্ট্র্যাটেজি B (Fibonacci চ্যানেল), strategy.py এই ফাইলের পাশে রাখো
except Exception as _e:
    FIB = None; FIB_ERR = str(_e)
PORT = int(os.environ.get("DASH_PORT", "8080"))
SET_FILE = os.path.join(HERE, "settings.json")
KEY_FILE = os.path.join(HERE, "exn_private.pem")
DAY_FILE = os.path.join(HERE, "bot_state.json")
SIG_LOG = os.path.join(HERE, "signals.log")
JOURNAL = os.path.join(HERE, "journal.jsonl")
TRACK_FILE = os.path.join(HERE, "track.json")
LEARN_FILE = os.path.join(HERE, "learn.json")

DEFAULTS = {"base_url": "", "api_key": "", "account_id": "", "instrument": "XAUUSD",
            "private_key_file": KEY_FILE,
            "dirs": "S", "lookback": 200, "bins": 16, "sma": 50, "tol": 0.30, "max_spread": 0.30,
            "sl": 3.0,
            "mode": "off", "risk": 1.0, "max_lot": 0.5, "max_daily_loss": 5.0, "max_trades_day": 0,
            "deviation": 30, "contract": 100.0, "poll": 1, "real_max_risk": 2.0, "entry": "bar",
            "tp_min": 2.0,
            "tf": "M5", "be_on": "on", "be_pct": 60.0, "be_buf": 0.10, "tp_pct": 100.0,
            "auto_learn": "on", "learn_min": 30, "learn_every": 10,
            "strat": "A", "b_tf": "H1", "b_sens": 18.0, "b_sma_on": "on", "b_sma": 50,
            "b_trail_on": "off", "b_trail_start": 1.0, "b_trail_usd": 8.0,
            "b_sl_type": "fixed", "b_sl_pct": 2.0, "b_hist_years": 3}
ENV_MAP = (("base_url", "EXN_BASE_URL"), ("api_key", "EXN_API_KEY"), ("account_id", "EXN_ACCOUNT_ID"),
           ("instrument", "EXN_INSTRUMENT"), ("private_key_file", "EXN_PRIVATE_KEY_FILE"))

LOCK = threading.Lock()
STATE = {"updated": None, "error": None, "is_demo": None, "account": None, "positions": [], "orders": 0,
         "price": None, "spread": None, "signal": None, "info": "", "bar": None, "history": [],
         "log": [], "mode": "off", "trades_live": 0, "trades_dry": 0, "start_equity": None,
         "trade": {"positions": [], "plan": None}, "learn": None, "journal": [], "score": None}


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def load_settings():
    c = dict(DEFAULTS)
    if os.path.exists(SET_FILE):
        try:
            saved = json.load(open(SET_FILE))
            for k, v in saved.items():
                if k in c and v is not None:
                    c[k] = v
            if "b_sl_type" not in saved:   # নতুন সংস্করণে প্রথমবার: B-র ট্রেইলিং বন্ধ, SL স্থির ২%
                c["b_trail_on"] = "off"
                write_private(SET_FILE, json.dumps(c, indent=1))
        except (OSError, ValueError):
            pass
    else:
        for k, e in ENV_MAP:
            if os.environ.get(e):
                c[k] = os.environ[e].strip()
        write_private(SET_FILE, json.dumps(c, indent=1))
    return c


CFG = load_settings()
CFG_VER = 0


def log(msg):
    line = f"{datetime.now(timezone.utc):%H:%M:%S} {msg}"
    print(line, flush=True)
    with LOCK:
        STATE["log"] = ([line] + STATE["log"])[:60]


# ------------------------------------------------------------------ API ক্লায়েন্ট
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_private_key(path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    raw = open(path, "rb").read().strip()
    if raw.startswith(b"-----BEGIN"):
        return load_pem_private_key(raw, password=None)
    txt = raw.decode().strip()
    try:
        seed = bytes.fromhex(txt)
    except ValueError:
        seed = base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))
    if len(seed) != 32:
        raise ValueError("প্রাইভেট কী সিড ৩২ বাইট হতে হবে (অথবা PEM দাও)")
    return Ed25519PrivateKey.from_private_bytes(seed)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def candle_row(c):
    b, a = c["bid"], c["ask"]
    return (parse_t(c["time"]), b["open"], b["high"], b["low"], b["close"],
            c.get("volume", 0), round(a["open"] - b["open"], 5))


class Api:
    def __init__(self, c):
        self.base = str(c["base_url"]).strip().rstrip("/")
        self.key = str(c["api_key"]).strip()
        self.acc = str(c["account_id"]).strip()
        self.inst = str(c["instrument"]).strip()
        self.sk = load_private_key(c["private_key_file"])

    def request(self, method, path, query=None, body=None, idem="", retries=3):
        qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in (query or {}).items())
        full = path + ("?" + qs if qs else "")
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        for attempt in range(retries):
            ts = int(time.time() * 1000)
            payload = json.dumps({
                "api_key": self.key, "idempotency_key": idem, "timestamp": ts, "sign_version": 1,
                "method": method, "path": full, "body_hash": b64u(hashlib.sha256(data).digest())},
                separators=(",", ":")).encode()
            headers = {"EXN-API-KEY": self.key, "EXN-IDEMPOTENCY-KEY": idem, "EXN-TIMESTAMP": str(ts),
                       "EXN-SIGN-VERSION": "1", "EXN-DATA": b64u(payload), "EXN-SIGN": b64u(self.sk.sign(payload)),
                       "User-Agent": "Mozilla/5.0 (Linux; Android 14) exness-bot/1.0"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(self.base + full, data=data if body is not None else None,
                                         headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    txt = r.read().decode()
                    return json.loads(txt) if txt else {}
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors="ignore")
                if e.code in (429, 503) and attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1)); continue
                raise RuntimeError(f"HTTP {e.code} {method} {path}: {txt[:300]}")
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(1.5); continue
                raise RuntimeError(f"নেটওয়ার্ক সমস্যা: {e}")

    def account(self):
        return self.request("GET", f"/v1/configuration/accounts/{self.acc}/account")

    def conditions(self):
        return self.request("GET", f"/v1/configuration/accounts/{self.acc}/instruments/{self.inst}/conditions")

    def snapshot(self):
        return self.request("GET", f"/v1/trading/accounts/{self.acc}/snapshot")

    def candles(self, tf, frm, to=None, count=None):
        q = {"instrument": self.inst, "timeframe": tf, "price_type": "both", "from": iso(frm)}
        if to is not None:
            q["to"] = iso(to)
        if count is not None:
            q["count"] = count
        return self.request("GET", f"/v1/market-data/accounts/{self.acc}/candles", q).get("candles", [])

    def open_position(self, side, volume, sl, tp, price=None, deviation=None):
        body = {"instrument": self.inst, "side": side, "volume": volume,
                "stop_loss_price": sl, "take_profit_price": tp, "comment": "vp-bot"}
        if price is not None:
            body["price"] = price
            if deviation is not None:
                body["deviation"] = str(deviation)
        return self.request("POST", f"/v1/trading/accounts/{self.acc}/positions",
                            body=body, idem="vp-" + uuid.uuid4().hex[:24])

    def modify_order(self, order_id, sl=None, tp=None):
        """চালু SLTP অর্ডার বদলানো: PATCH /orders/{id}। যে ফিল্ড দেওয়া হয়নি সেটা অপরিবর্তিত থাকে।"""
        body = {}
        if sl is not None:
            body["stop_loss_price"] = sl
        if tp is not None:
            body["take_profit_price"] = tp
        return self.request("PATCH", f"/v1/trading/accounts/{self.acc}/orders/{order_id}",
                            body=body, idem="vm-" + uuid.uuid4().hex[:24])

    def set_sltp(self, position_id, sl=None, tp=None):
        """পজিশনে নতুন SLTP অর্ডার বসানো: POST /orders, type=sltp।"""
        body = {"type": "sltp", "position_id": str(position_id), "comment": "vp-bot-be"}
        if sl is not None:
            body["stop_loss_price"] = sl
        if tp is not None:
            body["take_profit_price"] = tp
        return self.request("POST", f"/v1/trading/accounts/{self.acc}/orders",
                            body=body, idem="vs-" + uuid.uuid4().hex[:24])

    def close_position(self, position_id, volume=None, price=None, deviation=None):
        """DELETE /positions/{id}: volume না দিলে পুরোটা বন্ধ, দিলে আংশিক বন্ধ।"""
        q = {}
        if volume is not None:
            q["volume"] = volume
        if price is not None:
            q["price"] = price
            if deviation is not None:
                q["deviation"] = str(deviation)
        return self.request("DELETE", f"/v1/trading/accounts/{self.acc}/positions/{position_id}",
                            query=q or None, idem="vx-" + uuid.uuid4().hex[:24])

    def deals(self, frm, limit=200):
        q = {"from": iso(frm), "instrument": self.inst, "limit": limit}
        return self.request("GET", f"/v1/history/accounts/{self.acc}/deals", q).get("items", [])


class Feed:
    """স্ট্র্যাটেজি টাইমফ্রেমের ক্যান্ডেল ক্যাশ: প্রথমবার পিছনের কয়েক দিন, তারপর শুধু নতুন অংশ আনে।"""
    def __init__(self, tf="M5"):
        self.rows = {}
        self.tf = tf
        self.tfm = TF_MIN[tf]

    def _pull(self, api, start, end):
        step = timedelta(minutes=self.tfm * 900); cur = start
        while cur < end:
            nxt = min(cur + step, end)
            for c in api.candles(self.tf, cur, nxt):
                r = candle_row(c); self.rows[r[0]] = r
            cur = nxt

    def update(self, api, need):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if len(self.rows) < need:
            days = max(3, int(need * self.tfm / 1440 * 1.6) + 2)
            while True:
                self.rows = {}
                self._pull(api, now - timedelta(days=days), now)
                if len(self.rows) >= need or days >= 700:
                    break
                days = int(days * 1.5) + 2
        else:
            self._pull(api, max(self.rows) - timedelta(minutes=3 * self.tfm), now)
        keys = sorted(self.rows)
        for k in keys[:-3000]:
            del self.rows[k]
        return [self.rows[k] for k in sorted(self.rows)][-need:]


API = None
REAL_OK = False
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
CH_CACHE = {}
LEVELS = {}


def chart_data(tf, n):
    """চার্ট ট্যাবের ক্যান্ডেল (বিড OHLC + ভলিউম)। স্পেক অনুযায়ী count ঋণাত্মক = from-এর আগের n বার।"""
    api = API
    if api is None:
        raise RuntimeError("API সংযোগ নেই, আগে Settings ঠিক করো")
    hit = CH_CACHE.get((tf, n))
    if hit and time.time() - hit[0] < 2:
        return hit[1]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw = []
    try:
        raw = api.candles(tf, now, count=-n)
    except RuntimeError:
        raw = []
    if not raw:
        raw = api.candles(tf, now - timedelta(minutes=TF_MIN[tf] * n * 3), to=now)
    rows = [candle_row(c) for c in raw][-n:]
    out = {"tf": tf, "spread": rows[-1][6] if rows else None,
           "candles": [[int(r[0].timestamp()), r[1], r[2], r[3], r[4], r[5]] for r in rows]}
    CH_CACHE[(tf, n)] = (time.time(), out)
    return out


# ------------------------------------------------------------------ স্ট্র্যাটেজি
def profile(H, L, V, i, lb_n, bins, va=0.70):
    hi = H[i - lb_n:i]; lo = L[i - lb_n:i]; vol = V[i - lb_n:i]
    top, bot = hi.max(), lo.min()
    if top <= bot:
        return None
    bs = (top - bot) / bins
    f = np.clip(((lo - bot) / bs).astype(int), 0, bins - 1)
    e = np.clip(((hi - bot) / bs).astype(int), 0, bins - 1)
    w = np.where(vol > 0, vol / (e - f + 1), 0.0)
    diff = np.zeros(bins + 1)
    np.add.at(diff, f, w); np.add.at(diff, e + 1, -w)
    bv = np.cumsum(diff)[:bins]
    tot = bv.sum()
    if tot <= 0:
        return None
    pb = int(np.argmax(bv)); poc = bot + (pb + 0.5) * bs
    cov = bv[pb]; lo_b = hi_b = pb
    while cov < tot * va and (lo_b > 0 or hi_b < bins - 1):
        below = bv[lo_b - 1] if lo_b > 0 else -1
        above = bv[hi_b + 1] if hi_b < bins - 1 else -1
        if above >= below and hi_b < bins - 1:
            hi_b += 1; cov += bv[hi_b]
        elif lo_b > 0:
            lo_b -= 1; cov += bv[lo_b]
        else:
            break
    return poc, bot + lo_b * bs, bot + (hi_b + 1) * bs


def signal_on_last_bar(rows, a):
    tick = bool(getattr(a, "tick", False))
    O = np.array([r[1] for r in rows]); H = np.array([r[2] for r in rows])
    L = np.array([r[3] for r in rows]); C = np.array([r[4] for r in rows])
    V = np.array([r[5] for r in rows], dtype=float)
    i = len(rows) - 1
    if i < a.lookback + a.sma + 2:
        return None, "ডেটা কম"
    sp = rows[i][6]
    if a.max_spread and sp > a.max_spread:
        return None, f"স্প্রেড বেশি ({sp:.2f})"
    p = profile(H, L, V, i, a.lookback, a.bins)
    if p is None:
        return None, "প্রোফাইল হয়নি"
    poc, val, vah = p
    cur = C[i] if tick else O[i]
    sma = (C[i - a.sma + 1:i].sum() + cur) / a.sma
    global LEVELS
    LEVELS = {"poc": float(poc), "val": float(val), "vah": float(vah), "sma": float(sma),
              "tol": float(a.tol), "dirs": a.dirs}
    price = cur + sp / 2
    tol = a.tol
    buy = price > sma and (abs(price - val) <= tol or (abs(price - poc) <= tol and price >= poc))
    sell = (not buy) and price < sma and (abs(price - vah) <= tol or (abs(price - poc) <= tol and price <= poc))
    if a.dirs == "S": buy = False
    if a.dirs == "L": sell = False
    info = f"price={price:.3f} sma={sma:.3f} poc={poc:.3f} val={val:.3f} vah={vah:.3f} spread={sp:.2f}"
    sig = "buy" if buy else "sell" if sell else None
    if sig is None:
        if price > sma:
            if a.dirs == "S":
                why = "দাম SMA-র ওপরে → BUY-পক্ষ, কিন্তু dirs=S তাই BUY বন্ধ"
            else:
                why = (f"দাম SMA-র ওপরে → BUY-পক্ষ | VAL থেকে {abs(price - val):.2f} দূরে"
                       f" | POC থেকে {abs(price - poc):.2f} দূরে"
                       + ("" if price >= poc else " (দাম POC-র নিচে, POC-তে BUY হয় না)") + f" | সীমা tol={tol:g}")
        elif price < sma:
            if a.dirs == "L":
                why = "দাম SMA-র নিচে → SELL-পক্ষ, কিন্তু dirs=L তাই SELL বন্ধ"
            else:
                why = (f"দাম SMA-র নিচে → SELL-পক্ষ | VAH থেকে {abs(price - vah):.2f} দূরে"
                       f" | POC থেকে {abs(price - poc):.2f} দূরে"
                       + ("" if price <= poc else " (দাম POC-র ওপরে, POC-তে SELL হয় না)") + f" | সীমা tol={tol:g}")
        else:
            why = "দাম ঠিক SMA-তে"
        info += " | কারণ: " + why
    return sig, info


# ------------------------------------------------------------------ ট্রেড
DAY = {}


def day_state(now, eq):
    global DAY
    today = now.strftime("%Y-%m-%d")
    if not DAY:
        try:
            DAY = json.load(open(DAY_FILE))
        except (OSError, ValueError):
            DAY = {}
    if DAY.get("day") != today:
        DAY = {"day": today, "start_equity": eq, "trades": 0, "dry": 0}
        write_private(DAY_FILE, json.dumps(DAY))
    return DAY


def fmt_vol(x, step, vmin, vmax):
    step = Decimal(str(step))
    v = (Decimal(str(x)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    v = max(v, Decimal(str(vmin))); v = min(v, Decimal(str(vmax)))
    return format(v.normalize(), "f")


def pick_tp(sig, ref, cfg):
    """TP নির্ধারণ, তারপর tp_pct% দূরত্বে কমিয়ে আনা (শেখার অংশ এটা বদলাতে পারে)।"""
    tp, note = _pick_tp_raw(sig, ref, cfg)
    pct = float(cfg.get("tp_pct", 100.0))
    if tp is not None and pct < 100.0:
        tp = ref + (tp - ref) * pct / 100.0
        note += f" ×{pct:g}%"
    return tp, note


def _pick_tp_raw(sig, ref, cfg):
    """TP নির্ধারণ: এন্ট্রির দিকে পরের VAL/POC/VAH লেভেল; না থাকলে (None, কারণ)।"""
    lv = dict(LEVELS)
    names = [(n, lv[n]) for n in ("val", "poc", "vah") if lv.get(n) is not None]
    if not names:
        return None, "লেভেল এখনো হিসাব হয়নি"
    m = float(cfg.get("tp_min", 2.0))
    if sig == "buy":
        c = sorted((v, n) for n, v in names if v - ref >= m)
        if c:
            return c[0][0], c[0][1].upper()
    else:
        c = sorted(((v, n) for n, v in names if ref - v >= m), reverse=True)
        if c:
            return c[0][0], c[0][1].upper()
    return None, f"পরের VAL/POC/VAH লেভেল ন্যূনতম {m:g} দূরে নেই"


LAST_ORDER = {}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def sltp_for(pos, orders):
    """পজিশনের SL/TP: স্পেক অনুযায়ী এগুলো পজিশনে নয়, position_id মেলানো sltp অর্ডারে থাকে।"""
    sl, tp = pos.get("stop_loss_price"), pos.get("take_profit_price")
    pid = str(pos.get("position_id"))
    for o in orders or []:
        if o.get("position_id") is None or str(o.get("position_id")) != pid:
            continue
        if o.get("state") not in (None, "placed", "pending_new"):
            continue
        if sl is None and o.get("stop_loss_price") is not None:
            sl = o["stop_loss_price"]
        if tp is None and o.get("take_profit_price") is not None:
            tp = o["take_profit_price"]
    return _num(sl), _num(tp)


def plan_levels(sig, rows, cfg):
    """চার্টে দেখানোর জন্য সম্ভাব্য এন্ট্রি/SL/TP। handle_trade-এর হিসাবের প্রতিফলন, সেটা বদলালে এটাও বদলাও।"""
    sp = rows[-1][6]; bid = rows[-1][4]; ask = bid + sp
    sl = float(cfg["sl"])
    if sig == "sell":
        slp, ref = bid + sl + sp, bid
    else:
        slp, ref = ask - sl - sp, ask
    tp, tpn = pick_tp(sig, ref, cfg)
    return {"side": sig, "entry": ref, "sl": slp, "tp": tp, "tp_note": tpn}


def build_trade_view(api, snap, rows, sig, cfg):
    out = {"positions": [], "plan": None}
    orders = snap.get("orders") or []
    contract = float(cfg.get("contract", 100))
    bid = rows[-1][4] if rows else None
    sp = rows[-1][6] if rows else 0
    for p in (snap.get("positions") or []):
        if p.get("instrument") != api.inst or p.get("state") not in (None, "open"):
            continue
        side = p.get("direction") or p.get("side")
        op = _num(p.get("open_price")); vol = _num(p.get("volume"))
        sl, tp = sltp_for(p, orders); src = "api"
        try:
            t = parse_t(p["create_time"]).timestamp()
        except Exception:
            t = None
        lo = LAST_ORDER
        if sl is None and tp is None and lo and lo.get("side") == side and t is not None and abs(t - lo["t"]) < 180:
            sl, tp, src = lo["sl"], lo["tp"], "bot"
        pnl = None
        if bid is not None and op is not None and vol is not None:
            pnl = ((bid - op) if side == "buy" else (op - (bid + sp))) * vol * contract
        out["positions"].append({"id": p.get("position_id"), "side": side, "open": op, "volume": vol,
                                 "sl": sl, "tp": tp, "src": src, "pnl": pnl, "t": t})
    if sig and rows and not out["positions"]:
        try:
            out["plan"] = plan_levels(sig, rows, cfg)
        except Exception:
            pass
    return out


def _handle_trade(api, cfg, cond, sig, rows, snap, bal, eq, st):
    live = cfg["mode"] == "live"
    real = STATE["is_demo"] is False
    if live and (STATE["is_demo"] is None or (real and not REAL_OK)):
        log("লাইভ বন্ধ: অ্যাকাউন্টের ধরন নিশ্চিত নয়, DRY-RUN-এ চলছে")
        live = False
    tag = ("লাইভ-আসল" if real else "লাইভ") if live else "DRY-RUN"
    risk = float(cfg["risk"])
    if real and risk > float(cfg["real_max_risk"]):
        risk = float(cfg["real_max_risk"])
        log(f"আসল অ্যাকাউন্টে ঝুঁকি সর্বোচ্চ {risk}%-এ সীমিত")
    if eq < st["start_equity"] * (1 - float(cfg["max_daily_loss"]) / 100):
        log("দৈনিক লস সীমা ছুঁয়েছে, আজ আর ট্রেড নয়"); return
    if [p for p in (snap.get("positions") or []) if p.get("instrument") == api.inst]:
        log("পজিশন খোলা আছে, নতুন ট্রেড নয়"); return
    mtd = int(cfg["max_trades_day"])
    if mtd > 0 and (st["trades"] if live else st["dry"]) >= mtd:
        log("দৈনিক ট্রেড সীমা শেষ"); return
    digits = int(cond.get("point_digits", 3)); vmin = cond.get("volume_min", "0.01")
    vmax = cond.get("volume_max", "100"); vstep = cond.get("volume_step", "0.01")
    exec_mode = cond.get("exec_mode", "market")
    sp = rows[-1][6]; bid = rows[-1][4]; ask = bid + sp
    sl = float(cfg["sl"])
    if sig == "sell":
        slp, ref = bid + sl + sp, bid
    else:
        slp, ref = ask - sl - sp, ask
    tp, tpn = pick_tp(sig, ref, cfg)
    if tp is None:
        log(f"TP: {tpn}, ট্রেড বাদ"); return
    tpf = _pick_tp_raw(sig, ref, cfg)[0]
    lots = bal * risk / 100 / ((sl + sp) * float(cfg["contract"]))
    if lots < float(vmin):
        log(f"হিসাবকৃত লট {lots:.4f} ন্যূনতম {vmin}-এর কম, ঝুঁকি বেশি হয়ে যাবে, ট্রেড বাদ"); return
    vol = fmt_vol(min(lots, float(cfg["max_lot"])), vstep, vmin, vmax)
    f = lambda x: f"{x:.{digits}f}"
    log(f"[{tag}] {sig} vol={vol} SL={f(slp)} TP={f(tp)} ({tpn}) (ব্যালান্স {bal:g})")
    if not live:
        st["dry"] += 1; write_private(DAY_FILE, json.dumps(st)); return
    try:
        ack = api.open_position(sig, vol, f(slp), f(tp),
                                price=f(ref) if exec_mode == "instant" else None,
                                deviation=int(cfg["deviation"]) if exec_mode == "instant" else None)
        st["trades"] += 1; write_private(DAY_FILE, json.dumps(st))
        LAST_ORDER.update(side=sig, sl=float(slp), tp=float(tp), t=time.time(), sp=float(sp),
                          lv=dict(LEVELS), tpn=tpn, tpf=float(tpf))
        log(f"অর্ডার গৃহীত: {json.dumps(ack, ensure_ascii=False)[:200]}")
    except Exception as e:
        log(f"অর্ডার ত্রুটি: {e}")
        time.sleep(2)
        try:
            s2 = api.snapshot()
            if [p for p in (s2.get("positions") or []) if p.get("instrument") == api.inst]:
                st["trades"] += 1; write_private(DAY_FILE, json.dumps(st))
                log("ত্রুটি এলেও পজিশন খোলা পাওয়া গেছে, গণনায় ধরা হলো")
        except Exception as e2:
            log(f"পজিশন যাচাই ব্যর্থ: {e2}, নিজে চেক করো")


def handle_trade(api, cfg, cond, sig, rows, snap, bal, eq, st):
    """আসল অর্ডার গেলে True, না হলে False (তখন ছায়া ট্রেড নেওয়া হয়)।"""
    n0 = st["trades"]
    _handle_trade(api, cfg, cond, sig, rows, snap, bal, eq, st)
    return st["trades"] > n0


# ------------------------------------------------------------------ জার্নাল, ব্রেকইভেন, নিজে শেখা
# মডেল লাগে না। প্রতিটা ট্রেডের দামের পথ (path) রেখে, বন্ধ হওয়ার পরও সর্বোচ্চ SHADOW_SEC সেকেন্ড (বা আসল SL/TP ছোঁয়া পর্যন্ত) দাম
# দেখে। তারপর পুরনো ট্রেডগুলো অন্য BE%/TP% দিয়ে আবার চালিয়ে দেখে কোনটায় মোট লাভ বেশি হতো।
TRACK = {}
META = {"last_n": 0, "applied": [], "report": None, "pending": None, "b_report": None, "b_pending": None, "b_last_n": 0}
ML = threading.RLock()
PATH_CAP = 6000
SHADOW_SEC = 7200
BE_GRID = [40, 50, 60, 70, 80]
TP_GRID = [100, 90, 80, 70]
MGR_ERR = [None]
SAVE_T = [0.0]


def _jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_journal(limit=400):
    rows = []
    try:
        with open(JOURNAL) as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass
    return rows[-limit:]


def save_track():
    write_private(TRACK_FILE, json.dumps(TRACK))
    SAVE_T[0] = time.time()


def save_meta():
    write_private(LEARN_FILE, json.dumps(META, ensure_ascii=False))


def load_learn_state():
    global TRACK
    TRACK = _jload(TRACK_FILE, {})
    m = _jload(LEARN_FILE, {})
    with ML:
        for k in ("last_n", "applied", "report", "pending", "b_report", "b_pending", "b_last_n"):
            if k in m:
                META[k] = m[k]
    publish()


def publish():
    rows = read_journal(25)
    slim = [{k: r.get(k) for k in ("id", "t", "side", "open", "exit", "pnl", "outcome", "mfe", "mae", "be", "hr")}
            for r in rows][::-1]
    with ML:
        L = {"report": META.get("report"), "pending": META.get("pending"), "applied": META.get("applied", [])[:8],
             "open_tracks": len([1 for t in TRACK.values() if not t.get("done")]),
             "shadow": len([1 for t in TRACK.values() if t.get("done")]), "last_n": META.get("last_n", 0),
             "b_report": META.get("b_report"), "b_pending": META.get("b_pending")}
    try:
        sc = score_calc()
    except Exception as e:
        sc = None; log(f"স্কোরবোর্ড হিসাবে ত্রুটি: {str(e)[:100]}")
    with LOCK:
        STATE["learn"] = L
        STATE["journal"] = slim
        STATE["score"] = sc


def live_ok(cfg):
    if cfg["mode"] != "live":
        return False
    d = STATE["is_demo"]
    return not (d is None or (d is False and not REAL_OK))


def _sg(tr):
    return 1 if tr["side"] == "buy" else -1


def adopt(p, v, rows, cfg, now):
    pid = str(p.get("position_id"))
    side = p.get("direction") or p.get("side")
    try:
        t = parse_t(p["create_time"]).timestamp()
    except Exception:
        t = now.timestamp()
    lo = LAST_ORDER if (LAST_ORDER and LAST_ORDER.get("side") == side and abs(t - LAST_ORDER["t"]) < 180) else {}
    sl = v.get("sl") if v else None
    tp = v.get("tp") if v else None
    TRACK[pid] = {"id": pid, "side": side, "open": _num(p.get("open_price")), "vol": _num(p.get("volume")), "t": t,
                  "sl0": sl, "tp0": tp, "tpf": lo.get("tpf", tp), "sp": lo.get("sp", rows[-1][6]),
                  "lv": lo.get("lv", {}), "tpn": lo.get("tpn", ""),
                  "hr": datetime.fromtimestamp(t, timezone.utc).hour,
                  "be_used": float(cfg["be_pct"]) if cfg.get("be_on") == "on" else None,
                  "tp_used": float(cfg.get("tp_pct", 100.0)),
                  "tf": cfg.get("tf", "M5"), "shsec": max(SHADOW_SEC, 3 * TF_MIN.get(cfg.get("tf", "M5"), 5) * 60),
                  "mfe": 0.0, "mae": 0.0, "be": False, "be_sl": None, "be_n": 0, "be_t": 0.0, "be_ts": 0.0,
                  "path": [], "last": None, "gone": 0, "cl_n": 0, "done": None}
    save_track()
    log(f"ট্রেড ট্র্যাকিং শুরু: {side} #{pid}")


def push_path(tr, bid, ask, now):
    cur = [round(bid, 3), round(ask, 3)]
    if tr["last"] == cur:
        return
    tr["last"] = cur
    tr["path"].append([int(now.timestamp() - tr["t"])] + cur)
    if len(tr["path"]) > tr.get("pcap", PATH_CAP):
        del tr["path"][1::2]


def update_open(tr, v, bid, ask, now):
    sg = _sg(tr)
    px = bid if sg == 1 else ask
    if tr["sl0"] is None and v and v.get("sl") is not None:
        tr["sl0"] = v["sl"]
    if tr["tp0"] is None and v and v.get("tp") is not None:
        tr["tp0"] = v["tp"]
        if tr["tpf"] is None:
            tr["tpf"] = v["tp"]
    if tr["open"] is None:
        return
    fav = sg * (px - tr["open"])
    if tr["tp0"] is not None:
        D = abs(tr["tp0"] - tr["open"])
        if D > 0:
            tr["mfe"] = max(tr["mfe"], fav / D)
    if tr["sl0"] is not None:
        R = abs(tr["open"] - tr["sl0"])
        if R > 0:
            tr["mae"] = max(tr["mae"], -fav / R)
    push_path(tr, bid, ask, now)


def try_be(api, cfg, cond, snap, tr, v, bid, ask, now):
    """দাম TP-র be_pct% ছুঁলেই SL এন্ট্রির (+বাফার) জায়গায় আনে। রিভার্সালের অপেক্ষা করে না।"""
    if cfg.get("be_on") != "on" or not live_ok(cfg):
        return
    if tr["tp0"] is None or tr["open"] is None or tr["sl0"] is None:
        return
    sg = _sg(tr)
    px = bid if sg == 1 else ask
    tnow = now.timestamp()
    if tr["be"]:
        # অর্ডার ACK অ্যাসিঙ্ক, তাই ১৫ সেকেন্ড পর snapshot-এ SL সত্যিই বদলেছে কিনা মিলিয়ে দেখি
        if (v and v.get("src") == "api" and v.get("sl") is not None and tnow - tr["be_ts"] > 15
                and sg * (v["sl"] - tr["be_sl"]) < -0.02):
            log(f"ব্রেকইভেন #{tr['id']}: snapshot-এ SL বদলায়নি ({v['sl']}), আবার চেষ্টা হবে")
            tr["be"] = False
        return
    if tr["be_n"] >= 6 or tnow - tr["be_t"] < 4:
        return
    D = abs(tr["tp0"] - tr["open"])
    if D <= 0 or sg * (px - tr["open"]) / D < float(cfg["be_pct"]) / 100.0:
        return
    nsl = tr["open"] + sg * float(cfg["be_buf"])
    cur = v["sl"] if (v and v.get("sl") is not None) else tr["sl0"]
    if sg * (nsl - cur) <= 0:
        tr["be"] = True; tr["be_sl"] = cur; tr["be_ts"] = tnow
        return
    if sg * (px - nsl) < max(0.10, tr["sp"]):
        return
    digits = int(cond.get("point_digits", 3))
    fs = f"{nsl:.{digits}f}"
    oid = None
    for o in snap.get("orders") or []:
        if (o.get("order_type") == "sltp" and str(o.get("position_id")) == tr["id"]
                and o.get("state") in (None, "placed", "pending_new")):
            oid = o.get("order_id")
    tr["be_t"] = tnow; tr["be_n"] += 1
    try:
        if oid is not None:
            api.modify_order(oid, sl=fs)
            how = f"PATCH order {oid}"
        else:
            api.set_sltp(tr["id"], sl=fs, tp=f"{tr['tp0']:.{digits}f}")
            how = "নতুন SLTP অর্ডার"
        tr["be"] = True; tr["be_sl"] = float(fs); tr["be_ts"] = tnow
        log(f"ব্রেকইভেন #{tr['id']}: দাম TP-র {100 * sg * (px - tr['open']) / D:.0f}% ছুঁয়েছে, SL → {fs} ({how})")
    except Exception as e:
        log(f"ব্রেকইভেন ত্রুটি #{tr['id']}: {e}")


def close_info(api, tr):
    """বন্ধ হওয়া পজিশনের deals: (বন্ধের দাম, মোট নিট P/L (কমিশনসহ), বন্ধের সময়)। না পেলে None।"""
    items = api.deals(datetime.fromtimestamp(tr["t"] - 120, timezone.utc))
    mine = [it.get("deal") or {} for it in items]
    mine = [d for d in mine if str(d.get("position_id")) == tr["id"]]
    closes = [d for d in mine if d.get("type") == "close"]
    if not closes:
        return None
    pnl = sum((_num((d.get("profit") or {}).get("amount")) or 0.0) + (_num(d.get("commission")) or 0.0) for d in mine)
    try:
        tc = parse_t(closes[-1]["create_time"]).timestamp()
    except Exception:
        tc = time.time()
    return _num(closes[-1].get("price")), pnl, tc


def mark_closed(api, tr, now, cfg):
    sg = _sg(tr)
    est = False
    try:
        info = close_info(api, tr)
    except Exception as e:
        info = None
        log(f"deals আনা যায়নি: {str(e)[:120]}")
    if info is None or info[0] is None:
        tr["cl_n"] += 1
        if tr["cl_n"] < 10:
            return
        last = tr["path"][-1] if tr["path"] else None
        ex = (last[1] if sg == 1 else last[2]) if last else tr["open"]
        info = (ex, sg * (ex - tr["open"]) * tr["vol"] * float(cfg["contract"]), now.timestamp()); est = True
    ex, pnl, tc = info
    tr["path"].append([int(tc - tr["t"]), round(ex, 3), round(ex, 3)])
    tr["last"] = None
    tr["done"] = {"exit": ex, "pnl": pnl, "t": tc, "est": est}
    save_track()
    log(f"ট্রেড বন্ধ #{tr['id']}: P/L {pnl:+.2f}$ | MFE {100 * tr['mfe']:.0f}% | দাম এখনো কিছুক্ষণ ধরে দেখা হবে")


def classify(tr, ex):
    tol = max(0.15, 2 * tr["sp"])
    if tr["be"] and tr["be_sl"] is not None and abs(ex - tr["be_sl"]) <= tol:
        return "BE"
    if tr["tp0"] is not None and abs(ex - tr["tp0"]) <= tol:
        return "TP"
    if tr["sl0"] is not None and abs(ex - tr["sl0"]) <= tol:
        return "SL"
    return "OTHER"


def finalize(tr, cfg):
    d = tr["done"]; sg = _sg(tr)
    gross = sg * (d["exit"] - tr["open"]) * tr["vol"] * float(cfg["contract"])
    near, nd = None, None
    for nme in ("val", "poc", "vah"):
        x = (tr.get("lv") or {}).get(nme)
        if x is not None and (nd is None or abs(tr["open"] - x) < nd):
            near, nd = nme, abs(tr["open"] - x)
    row = {"id": tr["id"], "t": tr["t"], "tc": d["t"], "hr": tr["hr"], "side": tr["side"], "open": tr["open"],
           "exit": d["exit"], "vol": tr["vol"], "pnl": round(d["pnl"], 2), "comm": round(d["pnl"] - gross, 2),
           "sl0": tr["sl0"], "tp0": tr["tp0"], "tpf": tr["tpf"], "sp": tr["sp"], "tpn": tr["tpn"],
           "near": near, "ndist": None if nd is None else round(nd, 3),
           "mfe": round(tr["mfe"], 3), "mae": round(tr["mae"], 3), "outcome": classify(tr, d["exit"]),
           "tf": tr.get("tf", "M5"), "be": tr["be"], "be_used": tr["be_used"], "tp_used": tr["tp_used"], "est": d["est"],
           "src": "shadow" if tr.get("shadow") else "live", "path": tr["path"]}
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(row) + "\n")
    log(f"জার্নালে যোগ: #{tr['id']} {row['outcome']} {row['pnl']:+.2f}$ (MFE {100 * row['mfe']:.0f}%)")


def shadow_step(tr, cfg, bid, ask, now):
    """বন্ধ ট্রেডের পরের দামের পথ রাখে, যাতে 'অন্য সেটিংসে কী হতো' রিপ্লেতে ধরা যায়।"""
    push_path(tr, bid, ask, now)
    sg = _sg(tr)
    px = bid if sg == 1 else ask
    hit = ((tr["sl0"] is not None and sg * (px - tr["sl0"]) <= 0)
           or (tr["tpf"] is not None and sg * (tr["tpf"] - px) <= 0))
    return hit or now.timestamp() - tr["done"]["t"] > tr.get("shsec", SHADOW_SEC)


def manage(api, cfg, cond, snap, rows, tv, now):
    if not rows:
        return
    bid = rows[-1][4]; sp = rows[-1][6]; ask = bid + sp
    tvmap = {str(p["id"]): p for p in (tv or {}).get("positions", [])}
    open_ids = set()
    changed = False
    for p in snap.get("positions") or []:
        if p.get("instrument") != api.inst or p.get("state") not in (None, "open"):
            continue
        pid = str(p.get("position_id")); open_ids.add(pid)
        v = tvmap.get(pid)
        if pid not in TRACK:
            adopt(p, v, rows, cfg, now)
        tr = TRACK[pid]
        if tr.get("done"):
            continue
        tr["gone"] = 0
        update_open(tr, v, bid, ask, now)
        try_be(api, cfg, cond, snap, tr, v, bid, ask, now)
    for pid in list(TRACK):
        tr = TRACK[pid]
        if tr.get("done"):
            if shadow_step(tr, cfg, bid, ask, now):
                finalize(tr, cfg); del TRACK[pid]; changed = True
                maybe_learn(cfg)
        elif pid not in open_ids:
            tr["gone"] += 1
            if tr["gone"] >= 2:
                mark_closed(api, tr, now, cfg)
    if changed:
        publish(); save_track()
    elif time.time() - SAVE_T[0] > 20 and TRACK:
        save_track()


# ---- রিপ্লে
def simulate(tr, be_pct, tp_pct, buf, contract):
    """একই দামের পথে, অন্য BE%/TP% থাকলে ট্রেডের নিট P/L কত হতো।"""
    sg = 1 if tr["side"] == "buy" else -1
    o = tr["open"]; sl = tr["sl0"]
    tp = o + (tr["tpf"] - o) * tp_pct / 100.0
    D = abs(tp - o)
    moved = False
    ex = None
    px = None
    for _, b, a in tr["path"]:
        px = b if sg == 1 else a
        if sg * (px - sl) <= 0:
            ex = sl; break
        if sg * (tp - px) <= 0:
            ex = tp; break
        if be_pct and not moved and D > 0 and sg * (px - o) / D >= be_pct / 100.0:
            nsl = o + sg * buf
            if sg * (nsl - sl) > 0 and sg * (px - nsl) > 0:
                sl = nsl; moved = True
    if ex is None:
        ex = px if px is not None else tr["exit"]
    return sg * (ex - o) * tr["vol"] * contract + tr.get("comm", 0.0)


def _step(cur, best, k=10.0):
    return cur + max(-k, min(k, best - cur))


def learn(trades, cfg):
    contract = float(cfg["contract"]); buf = float(cfg["be_buf"])
    rep = {"time": datetime.now(timezone.utc).strftime("%m-%d %H:%M UTC"), "n": len(trades), "ok": False,
           "apply": None, "advice": [], "why": "", "top": []}
    use = [t for t in trades if t.get("tf", "M5") == cfg.get("tf", "M5") and t.get("sl0") is not None and t.get("tpf") is not None and t.get("path")
           and t.get("open") is not None]
    rep["n_used"] = len(use)
    if len(use) < 5:
        rep["why"] = "রিপ্লের উপযোগী ট্রেড ৫টার কম"
        return rep
    use.sort(key=lambda t: t["t"])
    sim = lambda t, be, tp: simulate(t, be, tp, buf, contract)
    total = lambda ts, be, tp: sum(sim(t, be, tp) for t in ts)
    actual = sum(t["pnl"] for t in use)
    scale = sum(abs(t["pnl"]) for t in use) or 1.0
    replay_actual = sum(sim(t, t.get("be_used"), t.get("tp_used") or 100.0) for t in use)
    fid = 100.0 * (1 - min(1.0, abs(replay_actual - actual) / scale))
    be_on = cfg.get("be_on") == "on"
    cur_be = float(cfg["be_pct"]) if be_on else None
    cur_tp = float(cfg.get("tp_pct", 100.0))
    k = max(1, int(len(use) * 0.7))
    train, test = use[:k], use[k:]
    cur = {"all": total(use, cur_be, cur_tp), "train": total(train, cur_be, cur_tp), "test": total(test, cur_be, cur_tp)}
    nobe = total(use, None, 100.0)
    grid = []
    for be in (BE_GRID if be_on else [None]):
        for tp in TP_GRID:
            grid.append({"be": be, "tp": tp, "all": total(use, be, tp), "train": total(train, be, tp),
                         "test": total(test, be, tp)})
    dist = lambda g: abs((g["be"] or 0) - (cur_be or 0)) + abs(g["tp"] - cur_tp)
    best = sorted(grid, key=lambda g: (-g["train"], dist(g)))[0]
    rep["top"] = [{"be": g["be"], "tp": g["tp"], "all": round(g["all"], 2)}
                  for g in sorted(grid, key=lambda g: -g["all"])[:6]]
    out = {}
    for t in use:
        out[t["outcome"]] = out.get(t["outcome"], 0) + 1
    sl_tr = [t for t in use if t["outcome"] == "SL"]
    near = [t for t in sl_tr if (t.get("mfe") or 0) >= 0.7]
    rep.update(actual=round(actual, 2), replay_actual=round(replay_actual, 2), fidelity=round(fid),
               cur={"be": cur_be, "tp": cur_tp, "all": round(cur["all"], 2)}, nobe=round(nobe, 2),
               outcomes=out, nearmiss=len(near), sl_count=len(sl_tr),
               best={"be": best["be"], "tp": best["tp"], "all": round(best["all"], 2),
                     "train": round(best["train"], 2), "test": round(best["test"], 2)})
    adv = rep["advice"]
    adv.append(f"{len(use)}টি ট্রেড: আসল ফল {actual:+.2f}$। এখনকার সেটিংসে (BE {cur_be or 'বন্ধ'}%, TP {cur_tp:g}%) রিপ্লেতে {cur['all']:+.2f}$, ব্রেকইভেন ছাড়া {nobe:+.2f}$।")
    if sl_tr:
        adv.append(f"{len(sl_tr)}টি SL-ট্রেডের {len(near)}টি TP-র ৭০%+ গিয়ে তারপর ফিরে SL খেয়েছে।")
    hrs = {}
    for t in use:
        h = hrs.setdefault(t["hr"], [0, 0.0]); h[0] += 1; h[1] += t["pnl"]
    for h, (c, s) in sorted(hrs.items(), key=lambda x: x[1][1]):
        if c >= 5 and s < 0:
            adv.append(f"UTC {h:02d}টা: {c}টি ট্রেডে মোট {s:+.2f}$ (শুধু তথ্য, বট নিজে ঘণ্টা বন্ধ করে না)।")
            break
    lv = {}
    for t in use:
        if t.get("near"):
            x = lv.setdefault(t["near"].upper(), [0, 0, 0.0]); x[0] += 1; x[1] += t["pnl"] > 0; x[2] += t["pnl"]
    if lv:
        adv.append("লেভেল অনুযায়ী: " + " | ".join(f"{n} {c}টি, জিত {w}, মোট {s:+.2f}$" for n, (c, w, s) in sorted(lv.items())))
    why = []
    if fid < 70:
        why.append(f"রিপ্লে আসলের সাথে মিলছে মাত্র {fid:.0f}% (ন্যূনতম ৭০%)")
    if len(test) < 8:
        why.append(f"যাচাইয়ের ট্রেড {len(test)}টি (ন্যূনতম ৮)")
    elif best["test"] < cur["test"]:
        why.append("সেরা সেটিং নতুন ট্রেডগুলোতে এখনকার চেয়ে খারাপ, তাই ওভারফিটিং সন্দেহ")
    gain = best["all"] - cur["all"]
    if gain < max(2.0, 0.05 * scale):
        why.append(f"লাভের পার্থক্য {gain:+.2f}$ যথেষ্ট নয় (ন্যূনতম {max(2.0, 0.05 * scale):.2f}$)")
    nb = cur_be if best["be"] is None or cur_be is None else _step(cur_be, best["be"])
    nt = _step(cur_tp, best["tp"])
    ch = {}
    if cur_be is not None and nb != cur_be:
        ch["be_pct"] = nb
    if nt != cur_tp:
        ch["tp_pct"] = nt
    if not why and ch:
        rep["apply"] = ch
        rep["ok"] = True
        adv.append(f"সাজেশন: {' ও '.join(('BE ' + format(cur_be, 'g') + '% → ' + format(nb, 'g') + '%') if k2 == 'be_pct' else ('TP ' + format(cur_tp, 'g') + '% → ' + format(nt, 'g') + '%') for k2 in ch)}। কারণ: সেরা সেটিং ({best['be'] or 'BE বন্ধ'}% / TP {best['tp']}%) রিপ্লেতে {best['all']:+.2f}$, এখনকার {cur['all']:+.2f}$। ধাপে ধাপে ১০ পয়েন্টের বেশি নড়ে না।")
    elif not why:
        adv.append("এখনকার সেটিংই রিপ্লেতে সেরা বা কাছাকাছি, কিছু বদলানোর দরকার নেই।")
    else:
        rep["why"] = "; ".join(why)
        adv.append("কিছু বদলানো হয়নি: " + rep["why"] + "।")
    return rep


def apply_changes(ch, why, auto):
    global CFG
    with LOCK:
        CFG = dict(CFG, **ch); c = dict(CFG)
    write_private(SET_FILE, json.dumps(c, indent=1))
    with ML:
        META["applied"] = ([{"time": datetime.now(timezone.utc).strftime("%m-%d %H:%M"), "auto": auto, "change": ch,
                             "why": why}] + META.get("applied", []))[:20]
        if "be_pct" in ch or "tp_pct" in ch:
            META["pending"] = None
        if any(k.startswith("b_") for k in ch):
            META["b_pending"] = None
        save_meta()
    log(f"শেখা: সেটিংস বদলানো হলো ({'অটো' if auto else 'হাতে'}) {ch}")
    publish()


def maybe_learn(cfg, force=False):
    trades = read_journal()
    n = len(trades)
    with ML:
        if not force and (cfg.get("auto_learn") != "on" or n < int(cfg["learn_min"])
                          or n - META.get("last_n", 0) < int(cfg["learn_every"])):
            return None
        with LOCK:
            cur = dict(CFG)
        rep = learn(trades, cur)
        META["last_n"] = n; META["report"] = rep; META["pending"] = rep.get("apply")
        save_meta()
    if not force and rep.get("apply") and STATE["is_demo"] is True:
        apply_changes(rep["apply"], rep["advice"][-1] if rep["advice"] else "", True)
    elif rep.get("apply"):
        log(f"শেখা: সাজেশন তৈরি {rep['apply']} (শিক্ষা ট্যাব থেকে প্রয়োগ করো)")
    else:
        log("শেখা: কোনো বদল দরকার নেই বা যথেষ্ট প্রমাণ নেই")
    publish()
    return rep


def force_off(why):
    global CFG
    with LOCK:
        CFG = dict(CFG, mode="off"); c = dict(CFG); STATE["mode"] = "off"
    write_private(SET_FILE, json.dumps(c, indent=1))
    log(f"ট্রেড মোড বন্ধ করা হলো: {why}")


# ------------------------------------------------------------------ ছায়া (ড্রাই রান) মোড
# মোড off না হলে A আর B দুটোর সিগন্যালই সবসময় ধরা হয়। আসল অর্ডার না গেলে (ড্রাই রান, ট্রেড চলছে, সীমা শেষ, লট কম,
# অন্য স্ট্র্যাটেজি সক্রিয়) প্রতিটা সিগন্যালে একটা কাল্পনিক ট্রেড চলে, একসাথে অনেকগুলো। লাইভ দামে আসল নিয়মেই।
SH_FILE = os.path.join(HERE, "shadow_state.json")
JOURNAL_SB = os.path.join(HERE, "journal_b_shadow.jsonl")
SHA = []          # A-র চলমান ছায়া ট্রেড (TRACK-এর মতো dict)
SHB = []          # B-র চলমান ছায়া ট্রেড
SHB_RAW = []      # ফাইল থেকে পড়া, পরের B সাইকেলে Trade হিসেবে গড়া হয়
SH_N = [0]
SH_T = [0.0]
ASH = {"feed": None, "tf": None, "last_bar": None, "fired_bar": None}


def sh_dump_b(w):
    tr = w["tr"]
    return {"id": w["id"], "t": w["t"], "side": tr.sig.side, "entry": tr.entry, "sl": tr.sl, "tps": list(tr.tps),
            "hit": tr.hit, "left": tr.left, "be": tr.be, "trail_on": tr.trail_on, "trail_stop": tr.trail_stop,
            "best": tr.best, "closed": tr.closed, "evs": w["evs"], "meta": w["meta"]}


def sh_save():
    try:
        write_private(SH_FILE, json.dumps({"n": SH_N[0], "A": SHA, "B": [sh_dump_b(w) for w in SHB] + SHB_RAW}))
    except Exception as e:
        log(f"ছায়া ট্রেড সেভ হয়নি: {str(e)[:100]}")
    SH_T[0] = time.time()


def sh_load():
    d = _jload(SH_FILE, {})
    SH_N[0] = int(d.get("n", 0))
    SHA[:] = d.get("A", [])
    SHB_RAW[:] = d.get("B", [])
    if SHA or SHB_RAW:
        log(f"ছায়া ট্রেড ফিরে এসেছে: A {len(SHA)}টি, B {len(SHB_RAW)}টি চলমান")


def sh_id(prefix):
    SH_N[0] += 1
    return f"{prefix}{SH_N[0]}"


# ---- A
def sha_open(cfg, cond, sig, rows, bal):
    if not rows:
        return
    sp = rows[-1][6]; bid = rows[-1][4]; ask = bid + sp
    sl = float(cfg["sl"])
    if sig == "sell":
        slp, ref = bid + sl + sp, bid
    else:
        slp, ref = ask - sl - sp, ask
    tp, tpn = pick_tp(sig, ref, cfg)
    if tp is None:
        log(f"[ছায়া A] TP: {tpn}, কাল্পনিক ট্রেড বাদ"); return
    tpf = _pick_tp_raw(sig, ref, cfg)[0]
    vmin = cond.get("volume_min", "0.01"); vmax = cond.get("volume_max", "100"); vstep = cond.get("volume_step", "0.01")
    lots = bal * float(cfg["risk"]) / 100 / ((sl + sp) * float(cfg["contract"])) if bal else 0.0
    vol = float(fmt_vol(max(float(vmin), min(lots, float(cfg["max_lot"]))), vstep, vmin, vmax))
    t = time.time()
    tf = cfg.get("tf", "M5")
    tr = {"id": sh_id("sa"), "side": sig, "open": ref, "vol": vol, "t": t, "sl0": slp, "tp0": tp, "tpf": tpf, "sp": sp,
          "lv": dict(LEVELS), "tpn": tpn, "hr": datetime.fromtimestamp(t, timezone.utc).hour,
          "be_used": float(cfg["be_pct"]) if cfg.get("be_on") == "on" else None, "tp_used": float(cfg.get("tp_pct", 100.0)),
          "tf": tf, "shsec": max(SHADOW_SEC, 3 * TF_MIN.get(tf, 5) * 60), "mfe": 0.0, "mae": 0.0, "be": False, "be_sl": None,
          "path": [], "last": None, "done": None, "shadow": True, "sl_cur": slp, "pcap": 1500}
    SHA.append(tr)
    log(f"[ছায়া A] কাল্পনিক {sig} #{tr['id']} entry={ref:.3f} SL={slp:.3f} TP={tp:.3f} ({tpn})")
    sh_save()


def sha_step(tr, cfg, bid, ask, now):
    sg = _sg(tr)
    update_open(tr, None, bid, ask, now)
    px = bid if sg == 1 else ask
    if cfg.get("be_on") == "on" and not tr["be"] and tr["tp0"] is not None:
        D = abs(tr["tp0"] - tr["open"])
        be_pct = tr["be_used"] if tr.get("be_used") else float(cfg["be_pct"])
        if D > 0 and sg * (px - tr["open"]) / D >= be_pct / 100.0:
            nsl = tr["open"] + sg * float(cfg["be_buf"])
            if sg * (nsl - tr["sl_cur"]) > 0 and sg * (px - nsl) > 0:
                tr["be"] = True; tr["be_sl"] = nsl; tr["sl_cur"] = nsl
    ex = None
    if sg * (px - tr["sl_cur"]) <= 0:
        ex = tr["sl_cur"]
    elif tr["tp0"] is not None and sg * (tr["tp0"] - px) <= 0:
        ex = tr["tp0"]
    if ex is not None:
        tr["path"].append([int(now.timestamp() - tr["t"]), round(ex, 3), round(ex, 3)])
        tr["last"] = None
        pnl = sg * (ex - tr["open"]) * tr["vol"] * float(cfg["contract"])
        tr["done"] = {"exit": ex, "pnl": pnl, "t": now.timestamp(), "est": False}
        log(f"[ছায়া A] কাল্পনিক ট্রেড বন্ধ #{tr['id']}: {pnl:+.2f}$ (MFE {100 * tr['mfe']:.0f}%)")


def sha_step_all(cfg, rows, now):
    if not SHA or not rows:
        return
    bid = rows[-1][4]; ask = bid + rows[-1][6]; changed = False
    for tr in list(SHA):
        try:
            if tr.get("done"):
                if shadow_step(tr, cfg, bid, ask, now):
                    finalize(tr, cfg); SHA.remove(tr); changed = True
                    maybe_learn(cfg)
            else:
                sha_step(tr, cfg, bid, ask, now)
        except Exception as e:
            log(f"[ছায়া A] ত্রুটি #{tr.get('id')}: {str(e)[:100]}")
    if changed:
        publish(); sh_save()
    elif time.time() - SH_T[0] > 20:
        sh_save()


def a_shadow_cycle(api, cfg, cond, now):
    """B সক্রিয় থাকলে A-র সিগন্যাল শুধু ছায়ার জন্য ধরা।"""
    a = SimpleNamespace(dirs=cfg["dirs"], lookback=int(cfg["lookback"]), bins=int(cfg["bins"]), sma=int(cfg["sma"]),
                        tol=float(cfg["tol"]), max_spread=float(cfg["max_spread"]), tick=(cfg.get("entry", "bar") == "tick"))
    if ASH["feed"] is None or ASH["tf"] != cfg["tf"]:
        ASH.update(feed=Feed(cfg["tf"]), tf=cfg["tf"], last_bar=None, fired_bar=None)
    feed = ASH["feed"]
    rows = feed.update(api, a.lookback + a.sma + 5)
    if not rows:
        return
    sig, info = signal_on_last_bar(rows, a)
    sha_step_all(cfg, rows, now)
    bar_t = rows[-1][0]; fresh = (now - bar_t).total_seconds() < feed.tfm * 60
    new = None
    if bar_t != ASH["last_bar"] and fresh:
        ASH["last_bar"] = bar_t
        if sig:
            ASH["fired_bar"] = bar_t; new = sig
    elif a.tick and sig and bar_t == ASH["last_bar"] and fresh and ASH["fired_bar"] != bar_t:
        ASH["fired_bar"] = bar_t; new = sig
    if new:
        bal = float((STATE.get("account") or {}).get("balance", 0) or 0)
        log(f"[ছায়া A] সিগন্যাল {new} | {info}")
        sha_open(cfg, cond, new, rows, bal)


# ---- B
def shb_build(d, p):
    sig = FIB.Signal(d["side"], d["t"], d["entry"], d["sl"], tuple(d["tps"]), 0, 0, 0)
    tr = FIB.Trade(sig, p)
    tr.hit = list(d["hit"]); tr.left = d["left"]; tr.be = d["be"]; tr.trail_on = d["trail_on"]
    tr.trail_stop = d["trail_stop"]; tr.best = d["best"]; tr.closed = d["closed"]
    return {"id": d["id"], "tr": tr, "t": d["t"], "evs": d["evs"], "meta": d["meta"]}


def shb_open(cfg, cond, sig, p, rows):
    sp = rows[-1][6]; bid = rows[-1][4]; ask = bid + sp
    long_ = sig.side == "long"
    entry = ask if long_ else bid
    sg = 1 if long_ else -1
    if sg * (entry - sig.sl) <= 0:
        log("[ছায়া B] SL দামের ভুল দিকে, কাল্পনিক ট্রেড বাদ"); return
    tps = tuple(FIB.round_tick(entry * (1 + sg * x / 100), p.tick) for x in p.tp_pct)
    tr = FIB.Trade(FIB.Signal(sig.side, time.time(), entry, sig.sl, tps, 0, 0, 0), p)
    meta = {"sl_type": cfg["b_sl_type"], "sl_pct": float(cfg["b_sl_pct"]), "trail": cfg["b_trail_on"],
            "trail_usd": float(cfg["b_trail_usd"]), "tf": cfg["b_tf"]}
    w = {"id": sh_id("sb"), "tr": tr, "t": time.time(), "evs": [], "meta": meta}
    SHB.append(w)
    log(f"[ছায়া B] কাল্পনিক {'buy' if long_ else 'sell'} #{w['id']} entry={entry:.3f} SL={sig.sl:.3f} TP1..4={list(tps)}")
    sh_save()


def shb_finish(w, now):
    tr = w["tr"]; sg = 1 if tr.long else -1
    dist = abs(tr.entry - tr.sl)
    mv = sum(sg * (e[2] - tr.entry) * e[3] for e in w["evs"])
    R = mv / dist if dist > 0 else 0.0
    row = {"id": w["id"], "strat": "B", "t": w["t"], "tc": now.timestamp(), "side": "buy" if tr.long else "sell",
           "entry": tr.entry, "sl": tr.sl, "R": round(R, 4), "events": [f"{e[0]}{e[1] or ''}@{e[2]:g}" for e in w["evs"]],
           **w["meta"]}
    with open(JOURNAL_SB, "a") as f:
        f.write(json.dumps(row) + "\n")
    log(f"[ছায়া B] কাল্পনিক ট্রেড শেষ #{w['id']}: {R:+.2f}R | {', '.join(row['events']) or '-'}")


def shb_step_all(cfg, p, rows, now, flip=False):
    if SHB_RAW:
        for d in SHB_RAW:
            try:
                SHB.append(shb_build(d, p))
            except Exception as e:
                log(f"[ছায়া B] পুরনো ট্রেড ফেরাতে ত্রুটি: {str(e)[:80]}")
        SHB_RAW.clear()
    if not SHB or not rows:
        return
    bid = rows[-1][4]; ask = bid + rows[-1][6]; changed = False
    for w in list(SHB):
        tr = w["tr"]; px = bid if tr.long else ask
        evs = tr.close_all(px) if flip else tr.step(px, px)
        for e in evs:
            w["evs"].append((e.kind, e.n, e.price, e.frac)); changed = True
        if tr.closed:
            shb_finish(w, now); SHB.remove(w)
    if changed:
        publish(); sh_save()
        maybe_learn_b(cfg)
    elif time.time() - SH_T[0] > 20:
        sh_save()


# ---- স্কোরবোর্ড
def _rows_jsonl(path, limit=20000):
    out = []
    try:
        with open(path) as f:
            for ln in f:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass
    return out[-limit:]


def _agg(items):
    """items: [(R, tc, is_shadow)]"""
    n = len(items)
    now = time.time()
    rs = [x[0] for x in items]
    last = [x[0] for x in items if x[1] and now - x[1] <= 30 * 86400]
    return {"n": n, "real": sum(1 for x in items if not x[2]), "shadow": sum(1 for x in items if x[2]),
            "win": round(100 * sum(1 for r in rs if r > 0) / n) if n else None,
            "avg": round(sum(rs) / n, 3) if n else None, "tot": round(sum(rs), 2),
            "d30": round(sum(last), 2), "n30": len(last)}


def score_calc():
    with LOCK:
        contract = float(CFG["contract"])
    a_items = []
    for r in read_journal(5000):
        try:
            dist = abs(r["open"] - r["sl0"]) * r["vol"] * contract
            if dist > 0:
                a_items.append((r["pnl"] / dist, r.get("tc"), r.get("src") == "shadow"))
        except (KeyError, TypeError):
            pass
    b_items = [(r["R"], r.get("tc"), True) for r in _rows_jsonl(JOURNAL_SB) if "R" in r]
    for r in _rows_jsonl(B_JOURNAL):
        try:
            dist = abs(r["entry"] - r["sl"]) * r["vol0"] * contract
            if dist > 0 and r.get("pnl") is not None:
                b_items.append((r["pnl"] / dist, r.get("tc"), False))
        except (KeyError, TypeError):
            pass
    A = _agg(a_items); B = _agg(b_items)
    A["open"] = len(SHA); B["open"] = len(SHB) + len(SHB_RAW) + len([1 for w in BST["trades"].values() if not w["tr"].closed])
    return {"A": A, "B": B}


# ---- B-র শেখা: ইতিহাসে SL% আর ট্রেইলিংয়ের গ্রিড চালিয়ে দেখা
B_SL_GRID = [("fixed", 1.0), ("fixed", 1.5), ("fixed", 2.0), ("fixed", 3.0), ("fib", 0.0)]
B_TR_GRID = [("off", 0.0), ("on", 10.0), ("on", 20.0)]
BL = threading.Lock()


def b_label(slt, slp, tro, tru):
    return ("SL " + (f"স্থির {slp:g}%" if slt == "fixed" else "Fib")) + " | ট্রেইলিং " + ("বন্ধ" if tro == "off" else f"${tru:g}")


def b_learn(cfg):
    rep = {"time": datetime.now(timezone.utc).strftime("%m-%d %H:%M UTC"), "n": 0, "ok": False, "apply": None,
           "advice": [], "why": "", "top": []}
    p0 = BST.get("p"); hist = BST.get("hist")
    if p0 is None or BST.get("hist_state") != "ready" or not hist:
        rep["why"] = "ইতিহাস এখনো প্রস্তুত নয়"; rep["advice"].append("কিছু বদলানো হয়নি: ইতিহাস এখনো নামেনি।"); return rep
    d = {r[0]: r for r in hist}
    for r in BST.get("recent", []):
        d[r[0]] = (r[0], r[1], r[2], r[3], r[4], r[5])
    ks = sorted(d)
    bars = [(k, d[k][1], d[k][2], d[k][3], d[k][4]) for k in ks]
    sp = {k: d[k][5] for k in ks}
    split = ks[int(len(ks) * 0.7)]
    cur = (cfg["b_sl_type"], float(cfg["b_sl_pct"]), cfg["b_trail_on"], float(cfg["b_trail_usd"]) if cfg["b_trail_on"] == "on" else 0.0)
    grid = []
    for slt, slp in B_SL_GRID:
        for tro, tru in B_TR_GRID:
            p = dataclasses.replace(p0, fixed_stop=(slt == "fixed"), sl_pct=slp, trail_enabled=(tro == "on"),
                                    trail_dist_usd=tru or p0.trail_dist_usd, pine_mode=False)
            tr = FIB.backtest_r(bars, p, sp)
            grid.append({"cfg": (slt, slp, tro, tru), "n": len(tr), "all": sum(t["R"] for t in tr),
                         "train": sum(t["R"] for t in tr if t["ts"] < split),
                         "test": sum(t["R"] for t in tr if t["ts"] >= split),
                         "ntest": sum(1 for t in tr if t["ts"] >= split)})
    curg = next((g for g in grid if g["cfg"] == cur), None)
    if curg is None:
        p = dataclasses.replace(p0, fixed_stop=(cur[0] == "fixed"), sl_pct=cur[1], trail_enabled=(cur[2] == "on"),
                                trail_dist_usd=cur[3] or p0.trail_dist_usd, pine_mode=False)
        tr = FIB.backtest_r(bars, p, sp)
        curg = {"cfg": cur, "n": len(tr), "all": sum(t["R"] for t in tr), "train": sum(t["R"] for t in tr if t["ts"] < split),
                "test": sum(t["R"] for t in tr if t["ts"] >= split), "ntest": sum(1 for t in tr if t["ts"] >= split)}
    best = sorted(grid, key=lambda g: -g["train"])[0]
    rep["n"] = curg["n"]
    rep["top"] = [{"label": b_label(*g["cfg"]), "all": round(g["all"], 2), "n": g["n"]} for g in sorted(grid, key=lambda g: -g["all"])[:6]]
    yrs = (ks[-1] - ks[0]) / 31536000
    rep["advice"].append(f"ইতিহাস {yrs:.1f} বছর, এখনকার সেটিংয়ে ({b_label(*cur)}) {curg['n']}টি ট্রেডে মোট {curg['all']:+.1f}R।")
    rep["advice"].append(f"সেরা (শেখার অংশে): {b_label(*best['cfg'])}, মোট {best['all']:+.1f}R ({best['n']}টি ট্রেড)।")
    why = []
    if curg["n"] < 30:
        why.append(f"ইতিহাসে এখনকার সেটিংয়ে ট্রেড {curg['n']}টি (ন্যূনতম ৩০)")
    if best["ntest"] < 8:
        why.append(f"যাচাইয়ের ট্রেড {best['ntest']}টি (ন্যূনতম ৮)")
    elif best["test"] < curg["test"]:
        why.append("সেরা সেটিং নতুন অংশে এখনকার চেয়ে খারাপ, ওভারফিটিং সন্দেহ")
    gain = best["all"] - curg["all"]
    if gain < 1.0:
        why.append(f"লাভের পার্থক্য {gain:+.1f}R যথেষ্ট নয় (ন্যূনতম ১R)")
    if best["cfg"] == cur:
        rep["advice"].append("এখনকার সেটিংই সেরা, কিছু বদলানোর দরকার নেই।")
    elif not why:
        slt, slp, tro, tru = best["cfg"]
        ch = {"b_sl_type": slt, "b_sl_pct": slp, "b_trail_on": tro}
        if tro == "on":
            ch["b_trail_usd"] = tru
        ch = {k: v for k, v in ch.items() if cfg.get(k) != v}
        if ch:
            rep["apply"] = ch; rep["ok"] = True
            rep["advice"].append(f"সাজেশন: {b_label(*best['cfg'])} ({gain:+.1f}R বেশি)।")
    else:
        rep["why"] = "; ".join(why)
        rep["advice"].append("কিছু বদলানো হয়নি: " + rep["why"] + "।")
    rep["advice"].append("(এটা ক্যান্ডেলের হিসাব, ভেতরে আগে SL না TP ছুঁল ধরা হয় না বলে আসল ফল একটু আলাদা হতে পারে।)")
    return rep


def b_count():
    return len(_rows_jsonl(JOURNAL_SB)) + len(_rows_jsonl(B_JOURNAL))


def maybe_learn_b(cfg, force=False, wait=False):
    def run():
        if not BL.acquire(blocking=False):
            return
        try:
            n = b_count()
            with ML:
                if not force and (cfg.get("auto_learn") != "on" or n - META.get("b_last_n", 0) < int(cfg["learn_every"])):
                    return
            with LOCK:
                cur = dict(CFG)
            rep = b_learn(cur)
            with ML:
                META["b_last_n"] = n; META["b_report"] = rep; META["b_pending"] = rep.get("apply")
                save_meta()
            if not force and rep.get("apply") and STATE["is_demo"] is True:
                apply_changes(rep["apply"], rep["advice"][-2] if len(rep["advice"]) > 1 else "", True)
            elif rep.get("apply"):
                log(f"[B] শেখা: সাজেশন তৈরি {rep['apply']} (শিক্ষা ট্যাব থেকে প্রয়োগ করো)")
            else:
                log("[B] শেখা: কোনো বদল দরকার নেই বা যথেষ্ট প্রমাণ নেই")
            publish()
        except Exception as e:
            log(f"[B] শেখায় ত্রুটি: {str(e)[:120]}")
        finally:
            BL.release()
    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ স্ট্র্যাটেজি B: Fibonacci চ্যানেল (IMBA)
# সিগন্যাল, TP/SL আর ট্রেইলিংয়ের নিয়ম strategy.py-তে। এখানে শুধু API-র সাথে জোড়া।
B_FILE = os.path.join(HERE, "b_state.json")
B_JOURNAL = os.path.join(HERE, "journal_b.jsonl")
BST = {"key": None, "eng": None, "feed": None, "last": None, "init": False, "pending": None, "trades": {}}


def b_params(cfg, cond):
    return FIB.Params(sensitivity=float(cfg["b_sens"]), sma_enabled=cfg["b_sma_on"] == "on",
                      sma_period=int(cfg["b_sma"]), trail_enabled=cfg["b_trail_on"] == "on",
                      fixed_stop=cfg["b_sl_type"] == "fixed", sl_pct=float(cfg["b_sl_pct"]),
                      trail_start_pct=float(cfg["b_trail_start"]), trail_dist_usd=float(cfg["b_trail_usd"]),
                      tick=10.0 ** -int(cond.get("point_digits", 3)), pine_mode=False)


def b_dump(w):
    tr = w["tr"]
    return {"side": tr.sig.side, "entry": tr.entry, "sl": tr.sl, "tps": list(tr.tps), "hit": tr.hit, "left": tr.left,
            "be": tr.be, "trail_on": tr.trail_on, "trail_stop": tr.trail_stop, "best": tr.best, "closed": tr.closed,
            "vol0": w["vol0"], "pend": w["pend"], "sent": w["sent"], "t": w["t"], "ev": w["ev"]}


def b_load(d, p):
    sig = FIB.Signal(d["side"], d["t"], d["entry"], d["sl"], tuple(d["tps"]), 0, 0, 0)
    tr = FIB.Trade(sig, p)
    tr.hit = list(d["hit"]); tr.left = d["left"]; tr.be = d["be"]; tr.trail_on = d["trail_on"]
    tr.trail_stop = d["trail_stop"]; tr.best = d["best"]; tr.closed = d["closed"]
    return {"tr": tr, "vol0": d["vol0"], "pend": d["pend"], "sent": d["sent"], "t": d["t"], "ev": d["ev"], "sl_t": 0.0}


def b_save():
    try:
        write_private(B_FILE, json.dumps({pid: b_dump(w) for pid, w in BST["trades"].items()}))
    except Exception as e:
        log(f"B স্টেট সেভ হয়নি: {e}")


def b_restore(p):
    out = {}
    try:
        for pid, d in json.load(open(B_FILE)).items():
            out[pid] = b_load(d, p)
    except (OSError, ValueError, KeyError):
        pass
    return out


def b_close(api, cfg, cond, pid, side, bid, ask, volume=None, why=""):
    """পুরো (volume=None) বা আংশিক বন্ধ। ইনস্ট্যান্ট এক্সিকিউশনে দাম আর deviation পাঠায়।"""
    digits = int(cond.get("point_digits", 3))
    price = dev = None
    if cond.get("exec_mode", "market") == "instant":
        price = f"{(bid if side == 'buy' else ask):.{digits}f}"; dev = int(cfg["deviation"])
    try:
        api.close_position(pid, volume, price, dev)
        log(f"[B] #{pid} {'আংশিক ' + str(volume) if volume else 'পুরো'} বন্ধের অনুরোধ গেছে {why}")
        return True
    except Exception as e:
        log(f"[B] বন্ধ করতে ত্রুটি #{pid}: {e}")
        return False


def b_partial(api, cfg, cond, pid, w, frac, cur_vol, side, bid, ask, tag):
    q = Decimal(str(cond.get("volume_step", "0.01"))); vmin = Decimal(str(cond.get("volume_min", "0.01")))
    want = Decimal(str(w["vol0"] * frac + w["pend"]))
    cv = (want / q).to_integral_value(rounding=ROUND_DOWN) * q
    rem = Decimal(str(cur_vol))
    if cv < vmin:
        w["pend"] = float(want)
        log(f"[B] {tag}: {want:.4f} লট ন্যূনতম {vmin}-এর কম, পরের TP-তে একসাথে বন্ধ হবে")
        return
    if rem - cv < vmin:
        if b_close(api, cfg, cond, pid, side, bid, ask, None, f"({tag}, বাকি অংশ ন্যূনতম লটের কম, পুরোটা)"):
            w["pend"] = 0.0
        return
    if b_close(api, cfg, cond, pid, side, bid, ask, format(cv.normalize(), "f"), f"({tag})"):
        w["pend"] = 0.0


def b_set_sl(api, snap, cond, pid, sl, tp):
    digits = int(cond.get("point_digits", 3)); fs = f"{sl:.{digits}f}"
    oid = None
    for o in snap.get("orders") or []:
        if (o.get("order_type") == "sltp" and str(o.get("position_id")) == pid
                and o.get("state") in (None, "placed", "pending_new")):
            oid = o.get("order_id")
    if oid is not None:
        api.modify_order(oid, sl=fs)
    else:
        api.set_sltp(pid, sl=fs, tp=f"{tp:.{digits}f}")
    return float(fs)


def b_journal(api, pid, w):
    row = {"id": pid, "t": w["t"], "tc": time.time(), "side": w["tr"].sig.side, "entry": w["tr"].entry,
           "sl": w["tr"].sl, "tps": list(w["tr"].tps), "vol0": w["vol0"], "events": w["ev"]}
    try:
        tmp = SimpleNamespace(**{"t": w["t"], "id": pid})
        info = close_info(api, {"t": w["t"], "id": pid})
        if info:
            row["exit"], row["pnl"] = info[0], round(info[1], 2)
    except Exception:
        pass
    with open(B_JOURNAL, "a") as f:
        f.write(json.dumps(row) + "\n")
    log(f"[B] ট্রেড শেষ #{pid}: P/L {row.get('pnl', '?')}$ | ঘটনা: {', '.join(w['ev']) or '-'}")


def b_manage(api, cfg, cond, snap, p, bid, ask, now):
    digits = int(cond.get("point_digits", 3)); tick = 10.0 ** -digits
    live = live_ok(cfg)
    orders = snap.get("orders") or []
    openp = {}
    for pos in snap.get("positions") or []:
        if pos.get("instrument") == api.inst and pos.get("state") in (None, "open"):
            openp[str(pos.get("position_id"))] = pos
    BT = BST["trades"]
    for pid, pos in openp.items():
        if pid in BT:
            continue
        side = pos.get("direction") or pos.get("side")
        op = _num(pos.get("open_price")); vol = _num(pos.get("volume"))
        sl, tp = sltp_for(pos, orders)
        pend = BST["pending"]
        if sl is None and pend is not None:
            sl = pend.sl
        if op is None or vol is None or sl is None:
            continue
        sg = 1 if side == "buy" else -1
        tps = tuple(FIB.round_tick(op * (1 + sg * x / 100), tick) for x in p.tp_pct)
        sig = FIB.Signal("long" if side == "buy" else "short", time.time(), op, sl, tps, 0, 0, 0)
        BT[pid] = {"tr": FIB.Trade(sig, p), "vol0": vol, "pend": 0.0, "sent": sl, "t": time.time(), "ev": [], "sl_t": 0.0}
        BST["pending"] = None
        log(f"[B] ট্র্যাকিং শুরু #{pid} {side} entry={op} SL={sl} TP1..4={list(tps)}")
        b_save()
    changed = False
    for pid in list(BT):
        w = BT[pid]; tr = w["tr"]
        if pid not in openp:
            b_journal(api, pid, w); del BT[pid]; changed = True
            continue
        pos = openp[pid]
        side = "buy" if tr.long else "sell"; sg = 1 if tr.long else -1
        cur_vol = _num(pos.get("volume")) or w["vol0"]
        px = bid if tr.long else ask
        evs = tr.step(px, px)
        for e in evs:
            w["ev"].append(f"{e.kind}{e.n or ''}@{e.price:g}"); changed = True
            if not live:
                continue
            if e.kind == "TP" and e.n < 4:
                b_partial(api, cfg, cond, pid, w, e.frac, cur_vol, side, bid, ask, f"TP{e.n}")
            elif e.kind == "TP":
                b_close(api, cfg, cond, pid, side, bid, ask, None, "(TP4, বাকি সব)")
            else:
                b_close(api, cfg, cond, pid, side, bid, ask, None, f"({e.kind} ছুঁয়েছে)")
        if tr.closed:
            continue
        if live and (tr.be or tr.trail_on):
            want = tr.stop_price()
            floor = tr.entry + sg * float(cfg["be_buf"])
            want = max(want, floor) if tr.long else min(want, floor)
            step = max(tick, p.trail_dist_usd / 8.0) if tr.trail_on else tick
            tnow = time.time()
            if (sg * (want - w["sent"]) >= step and sg * (px - want) >= max(0.10, ask - bid)
                    and tnow - w["sl_t"] >= 3):
                w["sl_t"] = tnow
                try:
                    w["sent"] = b_set_sl(api, snap, cond, pid, want, tr.tps[3])
                    log(f"[B] #{pid} SL → {w['sent']:.{digits}f} ({'ট্রেইলিং' if tr.trail_on else 'ব্রেকইভেন'})")
                    changed = True
                except Exception as e:
                    log(f"[B] SL বদলাতে ত্রুটি #{pid}: {e}")
    if changed:
        b_save()


def b_open(api, cfg, cond, sig, rows, snap, bal, eq, st):
    live = live_ok(cfg) if cfg["mode"] == "live" else False
    real = STATE["is_demo"] is False
    tag = ("লাইভ-আসল" if real else "লাইভ") if live else "DRY-RUN"
    risk = float(cfg["risk"])
    if real and risk > float(cfg["real_max_risk"]):
        risk = float(cfg["real_max_risk"])
    if eq < st["start_equity"] * (1 - float(cfg["max_daily_loss"]) / 100):
        log("[B] দৈনিক লস সীমা ছুঁয়েছে, আজ আর ট্রেড নয়"); return
    if [x for x in (snap.get("positions") or []) if x.get("instrument") == api.inst]:
        log("[B] পজিশন খোলা আছে, নতুন ট্রেড নয়"); return
    mtd = int(cfg["max_trades_day"])
    if mtd > 0 and (st["trades"] if live else st["dry"]) >= mtd:
        log("[B] দৈনিক ট্রেড সীমা শেষ"); return
    sp = rows[-1][6]; bid = rows[-1][4]; ask = bid + sp
    if cfg["max_spread"] and sp > float(cfg["max_spread"]):
        log(f"[B] স্প্রেড বেশি ({sp:.2f}), ট্রেড বাদ"); return
    side = "buy" if sig.side == "long" else "sell"
    ref = ask if side == "buy" else bid
    digits = int(cond.get("point_digits", 3)); f = lambda x: f"{x:.{digits}f}"
    slp = sig.sl
    dist = abs(ref - slp) + sp
    if (side == "buy" and slp >= ref) or (side == "sell" and slp <= ref) or dist <= 0:
        log("[B] SL দামের ভুল দিকে, ট্রেড বাদ"); return
    sg = 1 if side == "buy" else -1
    tp4 = FIB.round_tick(ref * (1 + sg * 4.0 / 100), 10.0 ** -digits)
    vmin = cond.get("volume_min", "0.01"); vmax = cond.get("volume_max", "100"); vstep = cond.get("volume_step", "0.01")
    lots = bal * risk / 100 / (dist * float(cfg["contract"]))
    if lots < float(vmin):
        log(f"[B] হিসাবকৃত লট {lots:.4f} ন্যূনতম {vmin}-এর কম (SL দূরত্ব ${dist:.2f}), ঝুঁকি বেশি হয়ে যাবে, ট্রেড বাদ"); return
    vol = fmt_vol(min(lots, float(cfg["max_lot"])), vstep, vmin, vmax)
    log(f"[B][{tag}] {side} vol={vol} SL={f(slp)} TP4={f(tp4)} (ব্যালান্স {bal:g}, SL দূরত্ব ${dist:.2f})")
    if not live:
        st["dry"] += 1; write_private(DAY_FILE, json.dumps(st)); return
    exec_mode = cond.get("exec_mode", "market")
    try:
        ack = api.open_position(side, vol, f(slp), f(tp4), price=f(ref) if exec_mode == "instant" else None,
                                deviation=int(cfg["deviation"]) if exec_mode == "instant" else None)
        st["trades"] += 1; write_private(DAY_FILE, json.dumps(st))
        BST["pending"] = sig
        log(f"[B] অর্ডার গৃহীত: {json.dumps(ack, ensure_ascii=False)[:200]}")
        return True
    except Exception as e:
        log(f"[B] অর্ডার ত্রুটি: {e}")
    return False


# ---- ইতিহাস থেকে "এই সেটআপ আগে কেমন চলেছে" (শুধু দেখায়, ট্রেড আটকায় না)
def b_hist_path(api, tf):
    return os.path.join(HERE, f"hist_{api.inst}_{tf}.csv")


def b_hist_sync(api, tf, years):
    """API থেকে ক্যান্ডেল নামিয়ে hist_<INSTR>_<TF>.csv-তে রাখে (এটা ব্যাকটেস্টের CSV হিসেবেও চলে)।
    আগের ফাইল থাকলে শুধু নতুন অংশ আনে। ফেরত: [(ts, o, h, l, c, spread), ...] পুরনো থেকে নতুন।"""
    tfm = TF_MIN[tf]; path = b_hist_path(api, tf); rows = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                t = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                rows[t] = (t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                           float(r.get("volume") or 0), float(r["spread"]))
    except (OSError, ValueError, KeyError):
        rows = {}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = now - timedelta(days=int(365 * years))
    start = cutoff
    if rows:
        start = max(cutoff, max(rows) - timedelta(minutes=3 * tfm))
    step = timedelta(minutes=tfm * 900); cur = start
    while cur < now:
        nxt = min(cur + step, now)
        for c in api.candles(tf, cur, nxt):
            r = candle_row(c); rows[r[0]] = r
        cur = nxt
    ks = sorted(k for k in rows if cutoff <= k and k.timestamp() + tfm * 60 <= now.timestamp())
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("time,open,high,low,close,volume,spread\n")
        for k in ks:
            r = rows[k]
            f.write(f"{k:%Y-%m-%d %H:%M:%S},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[6]}\n")
    os.replace(tmp, path)
    return [(k.timestamp(), rows[k][1], rows[k][2], rows[k][3], rows[k][4], rows[k][6]) for k in ks]


def b_hist_start(api, tf, years, key):
    BST["hist_state"] = "loading"; BST["hist_t"] = time.time()

    def bg():
        try:
            h = b_hist_sync(api, tf, years)
            if BST["key"] == key:
                BST["hist"] = h; BST["hist_state"] = "ready"
            log(f"[B] ইতিহাস প্রস্তুত: {len(h)} বার ({tf}), ফাইল {os.path.basename(b_hist_path(api, tf))}")
        except Exception as e:
            BST["hist_state"] = "error"
            log(f"[B] ইতিহাস নামাতে ত্রুটি: {str(e)[:120]}")
    threading.Thread(target=bg, daemon=True).start()


def b_stats_text(hist, recent, p, side):
    """hist + এখনকার ফিডের বন্ধ বার জোড়া দিয়ে একই ইঞ্জিন চালায়; একই সেটিংয়ে আগের সব ট্রেডের ফল।"""
    d = {r[0]: r for r in hist}
    for r in recent:
        d[r[0]] = (r[0], r[1], r[2], r[3], r[4], r[5])
    ks = sorted(d)
    bars = [(k, d[k][1], d[k][2], d[k][3], d[k][4]) for k in ks]
    tr = FIB.backtest_r(bars, p, {k: d[k][5] for k in ks})

    def one(tag, lst):
        st = FIB.trade_stats([t["R"] for t in lst])
        if st is None:
            return f"{tag}: আগের ডেটা নেই"
        return (f"{tag}: {st['n']} বার, জিতেছে {st['win']:.0f}% ({st['lo']:.0f}-{st['hi']:.0f}%), "
                f"গড় {st['avg']:+.2f}R (±{st['se95']:.2f}), মোট {st['tot']:+.1f}R")
    yrs = (ks[-1] - ks[0]) / 31536000 if len(ks) > 1 else 0
    return f"ইতিহাস ({yrs:.1f} বছর): " + one("সব", tr) + " | " + one("এই দিক", [t for t in tr if t["side"] == side])


def b_report(sig, p, recent):
    def bg():
        st = BST.get("hist_state")
        if st != "ready" or not BST.get("hist"):
            log("[B] ইতিহাস এখনো নামেনি, এবার সম্ভাবনা দেখানো গেল না"); return
        try:
            txt = b_stats_text(BST["hist"], recent, p, sig.side)
        except Exception as e:
            log(f"[B] ইতিহাসের হিসাবে ত্রুটি: {str(e)[:120]}"); return
        log("[B] " + txt)
        with LOCK:
            if STATE["history"]:
                STATE["history"][0]["info"] = (STATE["history"][0].get("info") or "") + " | " + txt
    threading.Thread(target=bg, daemon=True).start()


def b_cycle(api, cfg, cond, v, now, real=True):
    if FIB is None:
        raise RuntimeError("strategy.py পাওয়া যায়নি, এটা exness_all.py-র পাশে রাখো (" + FIB_ERR + ")")
    tf = cfg["b_tf"]; tfm = TF_MIN[tf]
    p = b_params(cfg, cond)
    key = (v, tf)
    if BST["key"] != key:
        BST.update(key=key, feed=Feed(tf), eng=FIB.SignalEngine(p), last=None, init=False, pending=None)
        BST["trades"] = b_restore(p)
        BST["hist"] = None
        b_hist_start(api, tf, int(cfg["b_hist_years"]), key)
        log(f"[B] স্ট্র্যাটেজি B চালু | {tf} | sens={p.sensitivity:g} SMA={'চালু ' + str(p.sma_period) if p.sma_enabled else 'বন্ধ'}"
            f" | ট্রেইলিং={'$' + format(p.trail_dist_usd, 'g') + ' (' + format(p.trail_start_pct, 'g') + '%-এ শুরু)' if p.trail_enabled else 'বন্ধ'}")
    feed, eng = BST["feed"], BST["eng"]
    if BST.get("hist_state") in ("ready", "error") and time.time() - BST.get("hist_t", 0) > 86400:
        b_hist_start(api, tf, int(cfg["b_hist_years"]), key)
    eng.p = p; BST["p"] = p
    rows = feed.update(api, p.lookback + int(cfg["b_sma"]) + 5)
    if real:
        snap = api.snapshot()
        ast = snap.get("account_state") or {}
        bal = float(ast.get("balance", 0) or 0); eq = float(ast.get("equity", bal) or bal)
        st = day_state(now, eq)
    else:
        snap = {}; ast = {}; bal = eq = 0.0; st = None
    nowt = now.timestamp()
    BST["recent"] = [(x[0].timestamp(), x[1], x[2], x[3], x[4], x[6]) for x in rows if x[0].timestamp() + tfm * 60 <= nowt]
    sig_now = None; info = BST.get("info", "")
    for r in rows:
        t = r[0].timestamp()
        if t + tfm * 60 > nowt or (BST["last"] is not None and t <= BST["last"]):
            continue
        s = eng.on_bar(t, r[1], r[2], r[3], r[4])
        BST["last"] = t
        if not BST["init"]:
            continue
        info = (f"B নতুন বার {r[0]:%H:%M} | ট্রেন্ড: {eng.flip or '-'} | সিগন্যাল: {s.side if s else '-'}"
                + (f" | SMA={s.sma:.2f}" if s and s.sma else ""))
        log(info)
        if eng.flip:
            if SHB or SHB_RAW:
                shb_step_all(cfg, p, rows, now, flip=True)
            for pid, w in (list(BST["trades"].items()) if real else []):
                if not w["tr"].closed and live_ok(cfg):
                    tr = w["tr"]
                    b_close(api, cfg, cond, pid, "buy" if tr.long else "sell", rows[-1][4], rows[-1][4] + rows[-1][6],
                            None, "(ট্রেন্ড বদলেছে)")
        if s:
            sig_now = "buy" if s.side == "long" else "sell"
            if real:
                with LOCK:
                    STATE["history"] = ([{"time": r[0].strftime("%m-%d %H:%M"), "signal": sig_now, "info": info}]
                                        + STATE["history"])[:30]
            open(SIG_LOG, "a").write(f"[{now:%Y-%m-%d %H:%M:%S}Z] {info}\n")
            age = nowt - (t + tfm * 60)
            if age > min(300.0, tfm * 30.0):
                log(f"[B] পুরনো সিগন্যাল বাদ: বার {age / 60:.1f} মিনিট আগে বন্ধ হয়েছে (সংযোগ বা API দেরি), এখন ট্রেড নেওয়া হবে না")
            elif cfg["mode"] != "off":
                placed = b_open(api, cfg, cond, s, rows, snap, bal, eq, st) if real else False
                if not placed:
                    shb_open(cfg, cond, s, p, rows)
            b_report(s, p, [(x[0].timestamp(), x[1], x[2], x[3], x[4], x[6]) for x in rows
                            if x[0].timestamp() + tfm * 60 <= nowt])
    if not BST["init"]:
        BST["init"] = True
        log("[B] ইতিহাস পড়া শেষ, এখন থেকে নতুন বন্ধ হওয়া বারে সিগন্যাল ধরা হবে")
    BST["info"] = info
    bid = rows[-1][4] if rows else None
    try:
        shb_step_all(cfg, p, rows, now)
    except Exception as e:
        log(f"[ছায়া B] ত্রুটি: {str(e)[:120]}")
    if not real:
        return
    if rows:
        ask = bid + rows[-1][6]
        try:
            b_manage(api, cfg, cond, snap, p, bid, ask, now)
        except Exception as e:
            m = str(e)[:150]
            if m != MGR_ERR[0]:
                MGR_ERR[0] = m; log(f"[B] ম্যানেজার ত্রুটি: {m}")
    try:
        tv = build_trade_view(api, snap, rows, None, cfg)
    except Exception:
        tv = {"positions": [], "plan": None}
    with LOCK:
        STATE.update(error=None, updated=now.strftime("%H:%M:%S") + " UTC", account=ast,
                     positions=snap.get("positions") or [], signal=sig_now, info=BST.get("info", "") or "B: অপেক্ষা",
                     mode=cfg["mode"], trades_live=st["trades"], trades_dry=st["dry"],
                     start_equity=st["start_equity"], trade=tv)
        if rows:
            STATE["price"] = rows[-1][4]; STATE["spread"] = rows[-1][6]; STATE["bar"] = rows[-1][0].strftime("%H:%M")


def configured(c):
    return bool(c["base_url"] and c["api_key"] and c["account_id"] and os.path.exists(c["private_key_file"]))


def worker():
    global API
    api = None; feed = None; cond = None; ver = -1; last_bar = None; warned = None; last_err = None; fired_bar = None; err_n = 0
    while True:
        poll = 10
        try:
            with LOCK:
                cfg = dict(CFG); v = CFG_VER
            poll = max(1, int(cfg["poll"]))
            if api is None or v != ver:
                api = None; API = None
                if not configured(cfg):
                    raise RuntimeError("Settings ট্যাবে API তথ্য আর প্রাইভেট কী দাও")
                tmp = Api(cfg)
                acc = tmp.account(); cond = tmp.conditions()
                api = tmp; API = tmp; feed = Feed(cfg['tf'])
                if v != ver:
                    last_bar = None; fired_bar = None
                ver = v
                with LOCK:
                    STATE["is_demo"] = acc.get("is_demo"); STATE["history"] = []
                log(f"সংযোগ ঠিক | {api.inst} | {'ডেমো' if acc.get('is_demo') is True else 'আসল/অজানা'} অ্যাকাউন্ট")
                if cfg["mode"] == "live" and acc.get("is_demo") is not True and not REAL_OK:
                    force_off("আসল অ্যাকাউন্টে লাইভ আবার নিশ্চিত করতে হবে"); cfg["mode"] = "off"
            if cfg.get("strat") == "B":
                nowb = datetime.now(timezone.utc)
                b_cycle(api, cfg, cond, v, nowb)
                if cfg["mode"] != "off" or SHA:
                    try:
                        a_shadow_cycle(api, cfg, cond, nowb)
                    except Exception as e:
                        m = str(e)[:150]
                        if m != MGR_ERR[0]:
                            MGR_ERR[0] = m; log(f"[ছায়া A] ত্রুটি: {m}")
                last_err = None; err_n = 0
                time.sleep(poll); continue
            a = SimpleNamespace(dirs=cfg["dirs"], lookback=int(cfg["lookback"]), bins=int(cfg["bins"]),
                                sma=int(cfg["sma"]), tol=float(cfg["tol"]), max_spread=float(cfg["max_spread"]),
                                tick=(cfg.get("entry", "bar") == "tick"))
            need = a.lookback + a.sma + 5
            now = datetime.now(timezone.utc)
            rows = feed.update(api, need)
            snap = api.snapshot()
            ast = snap.get("account_state") or {}
            bal = float(ast.get("balance", 0) or 0); eq = float(ast.get("equity", bal) or bal)
            st = day_state(now, eq)
            sig, info = (signal_on_last_bar(rows, a) if rows else (None, "ক্যান্ডেল আসেনি"))
            try:
                tv = build_trade_view(api, snap, rows, sig, cfg)
            except Exception:
                tv = {"positions": [], "plan": None}
            try:
                manage(api, cfg, cond, snap, rows, tv, now)
            except Exception as e:
                m = str(e)[:150]
                if m != MGR_ERR[0]:
                    MGR_ERR[0] = m; log(f"ম্যানেজার ত্রুটি: {m}")
            with LOCK:
                STATE.update(error=None, updated=now.strftime("%H:%M:%S") + " UTC", account=ast,
                             positions=snap.get("positions") or [], orders=len([o for o in (snap.get("orders") or []) if o.get("order_type") != "sltp" and o.get("state") in (None, "placed", "pending_new")]),
                             signal=sig, info=info, mode=cfg["mode"], trades_live=st["trades"],
                             trades_dry=st["dry"], start_equity=st["start_equity"], trade=tv)
                if rows:
                    STATE["price"] = rows[-1][4]; STATE["spread"] = rows[-1][6]
                    STATE["bar"] = rows[-1][0].strftime("%H:%M")
            last_err = None; err_n = 0
            if rows:
                bar_t = rows[-1][0]
                fresh = (now - bar_t).total_seconds() < feed.tfm * 60
                if bar_t != last_bar and fresh:
                    last_bar = bar_t; warned = None
                    line = f"নতুন বার {bar_t:%H:%M} | সিগন্যাল: {sig or '-'} | {info}"
                    log(line)
                    if sig:
                        with LOCK:
                            STATE["history"] = ([{"time": bar_t.strftime("%m-%d %H:%M"), "signal": sig,
                                                  "info": info}] + STATE["history"])[:30]
                        open(SIG_LOG, "a").write(f"[{now:%Y-%m-%d %H:%M:%S}Z] {line}\n")
                        fired_bar = bar_t
                        if cfg["mode"] != "off":
                            if not handle_trade(api, cfg, cond, sig, rows, snap, bal, eq, st):
                                sha_open(cfg, cond, sig, rows, bal)
                elif a.tick and sig and bar_t == last_bar and fresh and fired_bar != bar_t:
                    fired_bar = bar_t
                    line = f"বারের মধ্যে সিগন্যাল {bar_t:%H:%M} | সিগন্যাল: {sig} | {info}"
                    log(line)
                    with LOCK:
                        STATE["history"] = ([{"time": now.strftime("%m-%d %H:%M"), "signal": sig,
                                              "info": info}] + STATE["history"])[:30]
                    open(SIG_LOG, "a").write(f"[{now:%Y-%m-%d %H:%M:%S}Z] {line}\n")
                    if cfg["mode"] != "off":
                        if not handle_trade(api, cfg, cond, sig, rows, snap, bal, eq, st):
                            sha_open(cfg, cond, sig, rows, bal)
                elif not fresh and warned != bar_t:
                    warned = bar_t
                    log(f"সতর্কতা: সর্বশেষ {feed.tf} বার {bar_t:%m-%d %H:%M}, চলমান বার আসেনি। বাজার বন্ধ বা API দেরি, ট্রেড হবে না")
            try:
                sha_step_all(cfg, rows, now)
                if cfg["mode"] != "off" or SHB or SHB_RAW:
                    b_cycle(api, cfg, cond, v, now, real=False)
            except Exception as e:
                m = str(e)[:150]
                if m != MGR_ERR[0]:
                    MGR_ERR[0] = m; log(f"[ছায়া] ত্রুটি: {m}")
        except Exception as e:
            msg = str(e)[:300]
            with LOCK:
                STATE["error"] = msg
            if msg != last_err:
                log(f"ত্রুটি: {msg}"); last_err = msg
            err_n += 1
            if err_n % 5 == 0 and api is not None:
                log("কয়েকবার টানা ত্রুটি, API সংযোগ নতুন করে করা হচ্ছে (অ্যাকাউন্ট আর শর্ত আবার আনা হবে)")
                api = None; API = None
            poll = min(30, max(2, poll) * min(err_n, 10))
        time.sleep(poll)


# ------------------------------------------------------------------ সেটিংস
def public_cfg():
    with LOCK:
        c = dict(CFG)
    k = str(c["api_key"])
    out = {x: c[x] for x in DEFAULTS if x not in ("api_key", "private_key_file")}
    out["api_key_mask"] = ("••••" + k[-4:]) if k else ""
    out["key_saved"] = os.path.exists(c["private_key_file"])
    return out


STR_KEYS = ("base_url", "api_key", "account_id", "instrument", "dirs", "mode", "entry", "tf", "be_on", "auto_learn",
            "strat", "b_tf", "b_sma_on", "b_trail_on", "b_sl_type")
NUM_KEYS = (("real_max_risk", float), ("lookback", int), ("bins", int), ("sma", int), ("poll", int), ("max_trades_day", int),
            ("deviation", int), ("tol", float), ("max_spread", float), ("sl", float),
            ("risk", float), ("max_lot", float), ("max_daily_loss", float), ("contract", float), ("tp_min", float),
            ("be_pct", float), ("be_buf", float), ("tp_pct", float), ("learn_min", int), ("learn_every", int),
            ("b_sens", float), ("b_sma", int), ("b_trail_start", float), ("b_trail_usd", float), ("b_sl_pct", float), ("b_hist_years", int))


def apply_settings(d):
    global CFG, CFG_VER, REAL_OK
    with LOCK:
        c = dict(CFG)
        old = dict(CFG)
    prev_mode = c["mode"]
    for k in STR_KEYS:
        v = str(d.get(k, "")).strip()
        if v:
            c[k] = v
    for k, t in NUM_KEYS:
        v = str(d.get(k, "")).strip()
        if v != "":
            c[k] = t(v)
    if c["base_url"] and not (c["base_url"].startswith("https://") or c["base_url"].startswith(("http://127.0.0.1", "http://localhost"))):
        raise ValueError("Base URL https:// দিয়ে শুরু হতে হবে")
    if c["dirs"] not in ("S", "L", "B"):
        raise ValueError("দিক S, L বা B")
    if c.get("tp_min", 2.0) < 0:
        raise ValueError("tp_min >= 0")
    if c["tf"] not in TF_MIN:
        raise ValueError("স্ট্র্যাটেজি টাইমফ্রেম: " + ", ".join(TF_MIN))
    if c["be_on"] not in ("on", "off") or c["auto_learn"] not in ("on", "off"):
        raise ValueError("ব্রেকইভেন ও অটো-শেখা on বা off")
    if not (20 <= c["be_pct"] <= 95) or c["be_buf"] < 0 or not (50 <= c["tp_pct"] <= 100):
        raise ValueError("be_pct 20-95, be_buf >= 0, tp_pct 50-100")
    if c["learn_min"] < 10 or c["learn_every"] < 1:
        raise ValueError("learn_min >= 10, learn_every >= 1")
    if c.get("entry", "bar") not in ("bar", "tick"):
        raise ValueError("entry bar বা tick")
    if c["mode"] not in ("off", "dry", "live"):
        raise ValueError("মোড off, dry বা live")
    if c["lookback"] < 20 or c["sma"] < 2 or c["bins"] < 4 or c["poll"] < 1:
        raise ValueError("lookback>=20, sma>=2, bins>=4, poll>=1")
    if c["sl"] <= 0 or c["contract"] <= 0 or c["max_lot"] <= 0:
        raise ValueError("sl, contract, max_lot শূন্যের বেশি হতে হবে")
    if not (0 < c["risk"] <= 10) or not (0 < c["max_daily_loss"] <= 100) or c["max_trades_day"] < 0:
        raise ValueError("risk 0-10, max_daily_loss 0-100, max_trades_day>=0 (0 = সীমাহীন)")
    if c["strat"] not in ("A", "B") or c["b_tf"] not in TF_MIN or c["b_sma_on"] not in ("on", "off") or c["b_trail_on"] not in ("on", "off"):
        raise ValueError("স্ট্র্যাটেজি A বা B, B-র টাইমফ্রেম M1..H4, SMA ও ট্রেইলিং on বা off")
    if not (1 <= c["b_sens"] <= 100) or c["b_sma"] < 2 or c["b_trail_start"] <= 0 or c["b_trail_usd"] <= 0:
        raise ValueError("b_sens 1-100, b_sma>=2, ট্রেইলিং শুরু % আর ডলার দূরত্ব শূন্যের বেশি")
    if not (1 <= c["b_hist_years"] <= 10):
        raise ValueError("ইতিহাসের বছর ১ থেকে ১০")
    if c["b_sl_type"] not in ("fixed", "fib") or not (0 <= c["b_sl_pct"] <= 20) or (c["b_sl_type"] == "fixed" and c["b_sl_pct"] <= 0):
        raise ValueError("B-র SL ধরন fixed বা fib; স্থির SL-এ % শূন্যের বেশি (সর্বোচ্চ ২০)")
    if c["strat"] == "B" and FIB is None:
        raise ValueError("strategy.py পাওয়া যায়নি, এটা exness_all.py-র পাশে রাখো")
    if c["strat"] != old.get("strat", "A") and [x for x in (STATE.get("positions") or []) if x.get("instrument") == c["instrument"]]:
        raise ValueError("পজিশন খোলা আছে, স্ট্র্যাটেজি বদলাতে আগে ট্রেড বন্ধ হতে দাও")
    pk = str(d.get("private_key", "")).strip()
    creds_changed = bool(pk) or any(str(c[k]).strip() != str(old[k]).strip() for k in ("api_key", "account_id", "base_url"))
    if creds_changed and c["mode"] != "off":
        c["mode"] = "off"; REAL_OK = False
        log("API তথ্য বদলেছে, নিরাপত্তার জন্য ট্রেড মোড বন্ধ করা হলো। নতুন অ্যাকাউন্ট দেখে আবার চালু করো")
    if c["mode"] == "live" and prev_mode != "live":
        is_demo = STATE["is_demo"]
        if is_demo is None:
            raise ValueError("অ্যাকাউন্টের ধরন এখনো জানা যায়নি, কয়েক সেকেন্ড পরে আবার চেষ্টা করো")
        if is_demo is False:
            if str(d.get("confirm_account", "")).strip() != str(c["account_id"]).strip():
                raise ValueError("এটা আসল অ্যাকাউন্ট। অ্যাকাউন্ট নম্বর ঠিকভাবে লিখে নিশ্চিত করো")
            REAL_OK = True
    if pk:
        tmp = KEY_FILE + ".tmp"
        write_private(tmp, pk + "\n")
        try:
            load_private_key(tmp)
        except Exception:
            os.remove(tmp)
            raise ValueError("প্রাইভেট কী পড়া যায়নি (PEM বা ৩২-বাইট সিড দাও)")
        os.replace(tmp, KEY_FILE)
        c["private_key_file"] = KEY_FILE
    write_private(SET_FILE, json.dumps(c, indent=1))
    with LOCK:
        CFG = c; CFG_VER += 1
        STATE["error"] = None; STATE["mode"] = c["mode"]
    log(f"সেটিংস সেভ হয়েছে (মোড: {c['mode']})")


# ------------------------------------------------------------------ ওয়েব UI
PAGE = r'''<!doctype html><html lang="bn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Exness Bot</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--tx:#e8e8ea;--mut:#8b90a0;--ok:#2ecc71;--bad:#ff5c5c;--acc:#f5b942}
*{box-sizing:border-box}
body{margin:0 auto;background:var(--bg);color:var(--tx);font:15px/1.4 system-ui,sans-serif;padding:12px;max-width:640px}
h1{font-size:18px;margin:4px 0 10px}
.tabs{display:flex;gap:8px;margin-bottom:10px}
.tabs button{flex:1;padding:10px;border:0;border-radius:8px;background:#232733;color:var(--mut);font-size:14px}
.tabs button.on{background:var(--acc);color:#111;font-weight:600}
.card{background:var(--card);border-radius:10px;padding:12px;margin-bottom:10px}
.row{display:flex;justify-content:space-between;gap:8px;padding:3px 0}
.mut{color:var(--mut);font-size:13px}
.big{font-size:26px;font-weight:700}
.buy{color:var(--ok)}.sell{color:var(--bad)}
.err{color:var(--bad);font-size:13px;word-break:break-word}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;background:#2a2f3a}
label{display:block;margin-top:10px;font-size:13px;color:var(--mut)}
input,select,textarea{width:100%;padding:10px;margin-top:4px;border-radius:8px;border:1px solid #2a2f3a;background:#0f1115;color:var(--tx);font-size:15px}
textarea{height:100px;font-family:monospace;font-size:12px}
.save{width:100%;margin-top:14px;padding:12px;border:0;border-radius:8px;background:var(--acc);color:#111;font-size:16px;font-weight:600}
.h{font-size:12px;border-top:1px solid #2a2f3a;padding:6px 0;word-break:break-word}
.log{font:11px/1.5 monospace;word-break:break-word}
.hide{display:none}
.tb button{flex:1 1 30%}
h3{margin:16px 0 0;font-size:14px;color:var(--acc)}
.top{display:flex;align-items:center;gap:10px;margin-bottom:10px}.top h1{margin:0;font-size:18px}.top #pgname{margin-left:auto}
.burger{flex:none;width:42px;height:42px;border:0;border-radius:8px;background:#232733;color:var(--tx);font-size:20px}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:20}
.dr{position:fixed;top:0;bottom:0;left:0;width:250px;max-width:80vw;background:var(--card);z-index:30;transform:translateX(-102%);transition:transform .22s ease;padding:14px 12px;display:flex;flex-direction:column;gap:8px;box-shadow:4px 0 18px rgba(0,0,0,.5)}
.dr.open{transform:none}
.dr .drh{color:var(--acc);font-weight:700;font-size:16px;padding:6px 4px 10px}
.dr button{padding:14px;border:0;border-radius:8px;background:#232733;color:var(--mut);font-size:15px;text-align:left}
.dr button.on{background:var(--acc);color:#111;font-weight:600}
.dr .qs{display:flex;gap:8px;align-items:center}.dr .qs span{color:var(--mut);font-size:14px}.dr .qs button{flex:1;text-align:center;font-weight:700}
.tfwrap{position:relative;margin-bottom:10px}
.tfbtn{width:100%;display:flex;justify-content:space-between;padding:10px 14px;border:0;border-radius:8px;background:var(--acc);color:#111;font-size:14px;font-weight:600}
.tfmenu{position:absolute;left:0;right:0;top:100%;margin-top:4px;background:#232733;border-radius:8px;z-index:10;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,.5)}
.tfmenu button{display:block;width:100%;padding:12px 14px;border:0;background:none;color:var(--tx);text-align:left;font-size:14px}
.tfmenu button.on{background:var(--acc);color:#111;font-weight:600}
</style></head><body>
<div class="top"><button class="burger" onclick="drawer(true)" aria-label="মেনু">☰</button><h1>Exness Bot <span id="demo" class="tag"></span></h1><span id="pgname" class="mut">ড্যাশবোর্ড</span></div>
<div id="ov" class="ov hide" onclick="drawer(false)"></div>
<nav id="dr" class="dr"><div class="drh">মেনু</div><div class="qs"><span>চালাও:</span><button id="qsA" onclick="setStrat('A')">A</button><button id="qsB" onclick="setStrat('B')">B</button></div><div id="dmsg" class="mut"></div><button id="t1" class="on" onclick="tab(1)">ড্যাশবোর্ড</button><button id="t2" onclick="tab(2)">চার্ট</button><button id="t7" onclick="tab(7)">স্ট্র্যাটেজি A: Volume Profile</button><button id="t8" onclick="tab(8)">স্ট্র্যাটেজি B: Fibonacci</button><button id="t6" onclick="tab(6)">ব্রেকইভেন ও শেখা</button><button id="t4" onclick="tab(4)">শিক্ষা</button><button id="t3" onclick="tab(3)">Settings</button></nav>

<div id="p1">
<div class="card"><div class="mut">ট্রেড মোড</div>
<div class="tabs" style="margin:6px 0 0"><button id="m_off" onclick="setMode('off')">বন্ধ</button><button id="m_dry" onclick="setMode('dry')">DRY-RUN</button><button id="m_live" onclick="setMode('live')">লাইভ (ডেমো)</button></div>
<div class="mut" id="tr" style="margin-top:6px"></div></div>
<div class="card"><div class="mut">সিগন্যাল (<span id="bar">-</span> বার)</div>
<div class="big" id="sig">-</div><div class="mut" id="info"></div></div>
<div class="card"><div class="row"><span>বিড / আস্ক</span><b id="price">-</b></div>
<div class="row"><span>স্প্রেড</span><b id="spread">-</b></div>
<div class="row"><span>ব্যালান্স</span><b id="bal">-</b></div>
<div class="row"><span>ইকুইটি</span><b id="eq">-</b></div>
<div class="row"><span>আজকের P/L</span><b id="pnl">-</b></div>
<div class="row"><span>পেন্ডিং অর্ডার</span><b id="ord">-</b></div></div>
<div class="card"><div class="mut">ওপেন পজিশন</div><div id="pos">-</div></div>
<div class="card"><div class="mut">সিগন্যাল ইতিহাস</div><div id="hist">-</div></div>
<div class="card"><div class="mut">লগ</div><div class="log" id="log">-</div></div>
<div class="err" id="err"></div><div class="mut" id="upd"></div>
</div>

<div id="p2" class="hide">
<div class="tfwrap"><button id="tfbtn" class="tfbtn" onclick="tfMenu(event)"><span>5 মিনিট</span><span>▾</span></button>
<div id="tfmenu" class="tfmenu hide"><button data-tf="M1" onclick="setTF('M1')">1 মিনিট</button><button data-tf="M5" class="on" onclick="setTF('M5')">5 মিনিট</button><button data-tf="M15" onclick="setTF('M15')">15 মিনিট</button><button data-tf="M30" onclick="setTF('M30')">30 মিনিট</button><button data-tf="H1" onclick="setTF('H1')">1 ঘণ্টা</button><button data-tf="H4" onclick="setTF('H4')">4 ঘণ্টা</button></div></div>
<div class="card" style="padding:8px">
<div class="mut" id="ro" style="min-height:36px;font-size:12px">-</div>
<canvas id="cv" style="width:100%;height:340px;display:block;touch-action:none"></canvas>
<div class="tabs tb" style="margin-top:8px;flex-wrap:wrap"><button id="tlb" class="on" onclick="toggleTl()">SL/TP</button><button id="smab" class="on" onclick="toggleSma()">SMA</button><button id="lvb" class="on" onclick="toggleLv()">VAL/POC/VAH</button><button id="znb" class="on" onclick="toggleZn()">এন্ট্রি জোন</button><button id="aysb" onclick="MANUAL=false;YS=1;YOFF=0;draw()">অটো স্কেল</button><button onclick="IDXT=null;PAN=0;draw()">শেষ বার</button></div>
</div>
<div class="err" id="cerr"></div>
<div class="mut">XAUUSD বিড দাম। ছুঁয়ে বা টেনে যেকোনো ক্যান্ডেলের তথ্য দেখো।</div>
</div>

<div id="p4" class="hide">
<div class="card"><div class="mut">স্কোরবোর্ড (আসল + ছায়া ট্রেড মিলিয়ে, R = ঝুঁকির এক একক)</div><div id="sc_t" style="margin-top:6px">-</div></div>
<div class="card"><div class="mut">নিজে-শেখা (জার্নাল + রিপ্লে)</div>
<div id="lr_cur" class="mut" style="margin-top:6px">-</div>
<div class="tabs" style="margin:10px 0 0"><button onclick="learnRun()">এখনই বিশ্লেষণ</button><button id="lapply" onclick="learnApply()" class="hide">সাজেশন প্রয়োগ করো</button></div>
<div id="lmsg" class="mut" style="margin-top:6px"></div></div>
<div class="card"><div class="mut">সর্বশেষ বিশ্লেষণ <span id="lr_time"></span></div><div id="lr_adv" style="margin-top:6px">-</div></div>
<div class="card"><div class="mut">B-র শেখা (ইতিহাসে SL% ও ট্রেইলিং) <span id="lb_time"></span></div><div id="lb_adv" style="margin-top:6px">-</div></div>
<div class="card"><div class="mut">সেটিং তুলনা (রিপ্লেতে মোট $)</div><div id="lr_top">-</div></div>
<div class="card"><div class="mut">সেটিং কখন কী বদলেছে</div><div id="lr_app">-</div></div>
<div class="card"><div class="mut">জার্নাল (শেষ ২৫)</div><div id="lr_j">-</div></div>
<div class="mut">৩০টির আগে বট কিছু বদলায় না। ডেমোতে সাজেশন নিজে প্রয়োগ হয়, আসল অ্যাকাউন্টে শুধু বাটন চাপলে।</div>
</div>

<div id="p7" class="hide"><div class="card">
<h3 style="margin-top:0">স্ট্র্যাটেজি A: Volume Profile</h3>
<div class="mut" id="stA"></div>
<label>স্ট্র্যাটেজি টাইমফ্রেম (VAL/POC/VAH আর SMA এই টাইমফ্রেমের ক্যান্ডেলে হিসাব হয়)</label><select id="tf"><option value="M1">1 মিনিট</option><option value="M5">5 মিনিট</option><option value="M15">15 মিনিট</option><option value="M30">30 মিনিট</option><option value="H1">1 ঘণ্টা</option><option value="H4">4 ঘণ্টা</option></select>
<label>দিক</label><select id="dirs"><option value="S">শুধু Sell</option><option value="L">শুধু Buy</option><option value="B">দুটোই</option></select>
<label>Lookback</label><input id="lookback" inputmode="numeric">
<label>SMA</label><input id="sma" inputmode="numeric">
<label>Tolerance ($)</label><input id="tol" inputmode="decimal">
<label>SL ($)</label><input id="sl" inputmode="decimal">
<button class="save" onclick="save()">সেভ করো</button><div id="msg7" class="mut" style="margin-top:8px"></div>
</div></div>

<div id="p8" class="hide"><div class="card">
<h3 style="margin-top:0">স্ট্র্যাটেজি B: Fibonacci চ্যানেল (IMBA)</h3>
<div class="mut" id="stB"></div>
<label>B টাইমফ্রেম</label><select id="b_tf"><option value="M5">5 মিনিট</option><option value="M15">15 মিনিট</option><option value="M30">30 মিনিট</option><option value="H1">1 ঘণ্টা</option><option value="H4">4 ঘণ্টা</option></select>
<label>Sensitivity (লুকব্যাক = এটা × ১০ বার)</label><input id="b_sens" inputmode="decimal">
<label>SMA ফিল্টার (লং শুধু SMA-র ওপরে, শর্ট নিচে)</label><select id="b_sma_on"><option value="on">চালু</option><option value="off">বন্ধ</option></select>
<label>SMA পিরিয়ড</label><input id="b_sma" inputmode="numeric">
<h3>স্টপ লস</h3>
<label>SL-এর ধরন</label><select id="b_sl_type"><option value="fixed">স্থির % (এন্ট্রি থেকে)</option><option value="fib">Fib লেভেলে (০.৭৮৬ / ০.২৩৬)</option></select>
<label>SL % (স্থির হলে এন্ট্রি থেকে দূরত্ব, Fib হলে লেভেলের বাইরে বাড়তি)</label><input id="b_sl_pct" inputmode="decimal">
<h3>ইতিহাস থেকে সম্ভাবনা</h3>
<label>কত বছরের ইতিহাস নামাবে (সিগন্যালে "আগে কেমন চলেছে" দেখাবে)</label><input id="b_hist_years" inputmode="numeric">
<h3>ট্রেইলিং স্টপ</h3>
<label>ট্রেইলিং স্টপ</label><select id="b_trail_on"><option value="off">বন্ধ</option><option value="on">চালু</option></select>
<label>ট্রেইলিং কত % লাভে শুরু</label><input id="b_trail_start" inputmode="decimal">
<label>ট্রেইলিং দূরত্ব ($, দাম থেকে পিছনে)</label><input id="b_trail_usd" inputmode="decimal">
<button class="save" onclick="save()">সেভ করো</button><div id="msg8" class="mut" style="margin-top:8px"></div>
</div></div>

<input type="hidden" id="strat" value="A">
<div id="p6" class="hide"><div class="card">
<h3 style="margin-top:0">ব্রেকইভেন ও শেখা</h3>
<div class="mut">এই সেটিংস শুধু স্ট্র্যাটেজি A-র ট্রেডে কাজ করে। B-র ট্রেইলিং স্ট্র্যাটেজি B পাতায় আছে।</div>
<label>ব্রেকইভেন (দাম TP-র নির্দিষ্ট % ছুঁলে SL এন্ট্রিতে)</label><select id="be_on"><option value="on">চালু</option><option value="off">বন্ধ</option></select>
<label>ব্রেকইভেন কত % দূরত্বে</label><input id="be_pct" inputmode="decimal">
<label>এন্ট্রির ওপরে বাফার ($, কমিশন ঢাকতে)</label><input id="be_buf" inputmode="decimal">
<label>TP দূরত্বের কত % (১০০ = পুরোটা)</label><input id="tp_pct" inputmode="decimal">
<label>অটো-শেখা (ডেমোতে নিজে প্রয়োগ)</label><select id="auto_learn"><option value="on">চালু</option><option value="off">বন্ধ</option></select>
<label>শেখা শুরুর ন্যূনতম ট্রেড</label><input id="learn_min" inputmode="numeric">
<label>প্রতি কত নতুন ট্রেড পরপর শিখবে</label><input id="learn_every" inputmode="numeric">
<button class="save" onclick="save()">সেভ করো</button><div id="msg6" class="mut" style="margin-top:8px"></div>
</div></div>

<div id="p3" class="hide"><div class="card">
<h3 style="margin-top:0">API</h3>
<label>Base URL</label><input id="base_url" placeholder="https://..." autocomplete="off">
<label>API Key <span class="mut" id="km"></span></label><input id="api_key" type="password" autocomplete="new-password" placeholder="ফাঁকা রাখলে আগেরটাই থাকবে">
<label>Account ID</label><input id="account_id" inputmode="numeric" autocomplete="off">
<label>Instrument</label><input id="instrument" autocomplete="off">
<label>Private Key (PEM বা সিড) <span class="mut" id="pm"></span></label><textarea id="private_key" autocomplete="off" placeholder="ফাঁকা রাখলে আগেরটাই থাকবে"></textarea>
<h3>ট্রেড ও ঝুঁকি (A ও B দুটোতেই)</h3>
<label>Max spread ($, 0 = বন্ধ)</label><input id="max_spread" inputmode="decimal">
<label>প্রতি ট্রেডে ঝুঁকি (% ব্যালান্স)</label><input id="risk" inputmode="decimal">
<label>সর্বোচ্চ লট</label><input id="max_lot" inputmode="decimal">
<label>দৈনিক লস সীমা (%)</label><input id="max_daily_loss" inputmode="decimal">
<label>দৈনিক সর্বোচ্চ ট্রেড (০ = সীমাহীন)</label><input id="max_trades_day" inputmode="numeric">
<label>Contract size (গোল্ড = 100)</label><input id="contract" inputmode="decimal">
<label>সর্বনিম্ন TP দূরত্ব (দাম)</label><input id="tp_min" inputmode="decimal">
<label>এন্ট্রি কখন</label><select id="entry"><option value="bar">শুধু নতুন বারের শুরুতে</option><option value="tick">বারের মধ্যে যেকোনো সময় (প্রতি Refresh-এ)</option></select>
<label>Refresh (সেকেন্ড)</label><input id="poll" inputmode="numeric">
<button class="save" onclick="save()">সেভ করো</button><div id="msg" class="mut" style="margin-top:8px"></div>
</div>
<div class="mut">তথ্য শুধু এই ফোনে settings.json-এ সেভ হয় (শুধু তুমি পড়তে পারো)।</div></div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const NUM=["b_hist_years","b_sl_pct","b_sens","b_sma","b_trail_start","b_trail_usd","lookback","sma","tol","max_spread","sl","tp_min","risk","max_lot","max_daily_loss","max_trades_day","contract","poll","be_pct","be_buf","tp_pct","learn_min","learn_every"];
const PGN={1:"ড্যাশবোর্ড",2:"চার্ট",3:"Settings",4:"শিক্ষা",6:"ব্রেকইভেন ও শেখা",7:"স্ট্র্যাটেজি A",8:"স্ট্র্যাটেজি B"};
const setMsg=t=>{for(const i of ["msg","msg6","msg7","msg8","dmsg"]){const e=$(i);if(e)e.textContent=t;}};
function drawer(o){$("dr").classList.toggle("open",o);$("ov").classList.toggle("hide",!o);}
document.addEventListener("keydown",e=>{if(e.key=="Escape")drawer(false);});
let TX=null,TY=null;
document.addEventListener("touchstart",e=>{const t=e.touches[0];TX=t.clientX;TY=t.clientY;},{passive:true});
document.addEventListener("touchend",e=>{if(TX==null)return;const t=e.changedTouches[0],dx=t.clientX-TX,dy=Math.abs(t.clientY-TY);
 if(dy<40){if(TX<24&&dx>60)drawer(true);else if(dx<-60&&$("dr").classList.contains("open"))drawer(false);}TX=null;},{passive:true});
function tab(n){drawer(false);$("pgname").textContent=PGN[n];for(const i of [1,2,3,4,6,7,8]){$("p"+i).classList.toggle("hide",n!=i);$("t"+i).classList.toggle("on",n==i);}
if(n==2){loadChart();}if(n==3||n==6||n==7||n==8)loadSettings();if(n==4)load();}
function render(s){
 const g=s.signal;$("sig").textContent=g?g.toUpperCase():"-";$("sig").className="big "+(g||"");
 $("info").textContent=s.info||"";$("bar").textContent=s.bar||"-";
 $("price").textContent=s.price==null?"-":(s.price.toFixed(2)+" / "+(s.spread==null?"-":(s.price+s.spread).toFixed(2)));$("spread").textContent=s.spread==null?"-":s.spread;
 const a=s.account||{};$("bal").textContent=a.balance||"-";$("eq").textContent=a.equity||"-";$("ord").textContent=s.orders;
 const pl=(s.start_equity!=null&&a.equity)?(parseFloat(a.equity)-s.start_equity):null;
 $("pnl").textContent=pl==null?"-":pl.toFixed(2);$("pnl").className=pl==null?"":(pl>=0?"buy":"sell");
 ISDEMO=s.is_demo;$("demo").textContent=s.is_demo===true?"DEMO":(s.is_demo===false?"REAL ACCOUNT":"");
 $("demo").style.background=s.is_demo===false?"#7a1f1f":"";$("m_live").textContent=s.is_demo===false?"লাইভ (আসল)":"লাইভ (ডেমো)";
 for(const m of ["off","dry","live"])$("m_"+m).classList.toggle("on",s.mode==m);
 $("tr").textContent="আজ ট্রেড: লাইভ "+s.trades_live+" | dry-run "+s.trades_dry;
 const TV=(s.trade&&s.trade.positions)||[],f2=v=>v==null?"-":Number(v).toFixed(2);
 $("pos").innerHTML=s.positions.length?s.positions.map(p=>{const t=TV.find(x=>String(x.id)==String(p.position_id))||{};
  return "<div class='h'><b>"+esc(p.instrument)+" "+esc(p.direction||p.side)+" "+esc(p.volume)+"</b> @ "+esc(p.open_price)+"<br>SL "+f2(t.sl)+" | TP "+f2(t.tp)+(t.pnl==null?"":" | ≈ "+t.pnl.toFixed(2)+"$")+"<br><span class='mut'>"+esc(JSON.stringify(p))+"</span></div>";}).join(""):"<span class='mut'>নেই</span>";
 $("hist").innerHTML=s.history.length?s.history.map(h=>"<div class='h'><b class='"+esc(h.signal)+"'>"+esc(h.signal)+"</b> "+esc(h.time)+"<br><span class='mut'>"+esc(h.info)+"</span></div>").join(""):"<span class='mut'>এখনো নেই</span>";
 $("log").innerHTML=s.log.length?s.log.slice(0,25).map(esc).join("<br>"):"-";
 $("err").textContent=s.error||"";$("upd").textContent=s.updated?"আপডেট: "+s.updated:"";
 renderLearn(s);
}
const OC={TP:"buy",SL:"sell",BE:"",OTHER:""};
function renderScore(s){const S=s.score;if(!S)return;const f=(n,x)=>"<div class='row'><span><b>"+n+"</b> "+x.n+"টি (আসল "+x.real+", ছায়া "+x.shadow+") | খোলা "+x.open+"</span><b class='"+((x.tot||0)>=0?"buy":"sell")+"'>"+(x.n?(x.win+"% জিত | গড় "+(x.avg>=0?"+":"")+x.avg+"R | মোট "+(x.tot>=0?"+":"")+x.tot+"R | ৩০দিন "+(x.d30>=0?"+":"")+x.d30+"R"):"ডেটা নেই")+"</b></div>";$("sc_t").innerHTML=f("A",S.A)+f("B",S.B);}
function renderLearn(s){renderScore(s);const L=s.learn;if(!L)return;const R=L.report;const RB=L.b_report;
  $("lb_time").textContent=RB?"("+RB.time+")":"";
  $("lb_adv").innerHTML=RB?RB.advice.map(a=>"<div class='h'>"+esc(a)+"</div>").join("")+(RB.top&&RB.top.length?"<div class='h mut'>সেরা ৬: "+RB.top.map(t=>esc(t.label)+" "+t.all+"R").join(" | ")+"</div>":""):"<span class='mut'>এখনো বিশ্লেষণ হয়নি</span>";
 $("lr_cur").textContent="জার্নালে "+(L.last_n||0)+"টি পর্যন্ত বিশ্লেষিত | চলমান ট্র্যাক "+L.open_tracks+" | বন্ধের পর দাম দেখা হচ্ছে "+L.shadow;
 const PD=Object.assign({},L.pending||{},L.b_pending||{});$("lapply").classList.toggle("hide",!Object.keys(PD).length);
 if(Object.keys(PD).length)$("lapply").textContent="সাজেশন প্রয়োগ: "+Object.entries(PD).map(([k,v])=>k+"="+v).join(", ");
 $("lr_time").textContent=R?"("+R.time+")":"";
 $("lr_adv").innerHTML=R?R.advice.map(a=>"<div class='h'>"+esc(a)+"</div>").join("")+(R.fidelity!=null?"<div class='h mut'>রিপ্লে আসলের সাথে "+R.fidelity+"% মিলেছে</div>":""):"<span class='mut'>এখনো বিশ্লেষণ হয়নি</span>";
 $("lr_top").innerHTML=R&&R.top&&R.top.length?R.top.map(t=>"<div class='row'><span>BE "+(t.be==null?"বন্ধ":t.be+"%")+" / TP "+t.tp+"%</span><b class='"+(t.all>=0?"buy":"sell")+"'>"+t.all.toFixed(2)+"</b></div>").join(""):"<span class='mut'>-</span>";
 $("lr_app").innerHTML=L.applied.length?L.applied.map(a=>"<div class='h'>"+esc(a.time)+" "+(a.auto?"অটো":"হাতে")+": "+esc(JSON.stringify(a.change))+"</div>").join(""):"<span class='mut'>এখনো কিছু বদলায়নি</span>";
 const J=s.journal||[];
 $("lr_j").innerHTML=J.length?J.map(j=>"<div class='row'><span><b class='"+(j.side=="buy"?"buy":"sell")+"'>"+esc(j.side)+"</b> "+esc(j.outcome)+(j.be?" (BE)":"")+" MFE "+Math.round(100*(j.mfe||0))+"%</span><b class='"+(j.pnl>=0?"buy":"sell")+"'>"+Number(j.pnl).toFixed(2)+"</b></div>").join(""):"<span class='mut'>এখনো ট্রেড নেই</span>";
}
async function learnRun(){$("lmsg").textContent="বিশ্লেষণ চলছে…";const r=await fetch("/api/learn/run",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});const j=await r.json();$("lmsg").textContent=j.ok?"হয়েছে":("ত্রুটি: "+j.error);load();}
async function learnApply(){if(!confirm("সাজেশন প্রয়োগ করবে?"))return;const r=await fetch("/api/learn/apply",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});const j=await r.json();$("lmsg").textContent=j.ok?"প্রয়োগ হয়েছে":("ত্রুটি: "+j.error);load();}
async function load(){try{const r=await fetch("/api/status");render(await r.json());}catch(e){$("err").textContent="সার্ভারে সংযোগ নেই";}}
async function post(d){const r=await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});return r.json();}
let ISDEMO=null;
async function setMode(m){
 const d={mode:m};
 if(m=="live"){
  if(ISDEMO===true){if(!confirm("ডেমো অ্যাকাউন্টে অর্ডার যাবে। নিশ্চিত?"))return;}
  else if(ISDEMO===false){const a=prompt("এটা আসল টাকার অ্যাকাউন্ট। চালু করতে অ্যাকাউন্ট নম্বরটা লেখো:");if(!a)return;d.confirm_account=a;}
  else{alert("অ্যাকাউন্টের ধরন এখনো জানা যায়নি, একটু পরে চেষ্টা করো");return;}
 }
 const j=await post(d);if(!j.ok)alert(j.error);load();}
function markStrat(){const s=$("strat").value;for(const x of ["A","B"]){$("qs"+x).classList.toggle("on",s==x);const e=$("st"+x);if(e)e.textContent=s==x?"● এখন এই স্ট্র্যাটেজি চলছে":"○ এখন চলছে না (সেটিং সেভ থাকে)";}}
async function setStrat(x){await loadSettings();if($("strat").value!=x){$("strat").value=x;await save();await loadSettings();}}
async function loadSettings(){const r=await fetch("/api/settings");const c=await r.json();
 const DEF={tf:"M5",entry:"bar",strat:"A",b_tf:"H1",be_on:"on",auto_learn:"on",b_sma_on:"on",b_trail_on:"off",b_sl_type:"fixed"};
 for(const k of ["base_url","account_id","instrument","dirs","entry","tf","be_on","auto_learn","strat","b_tf","b_sma_on","b_trail_on","b_sl_type"].concat(NUM))$(k).value=(c[k]!==undefined&&c[k]!==null&&c[k]!=="")?c[k]:(DEF[k]||"");
 markStrat();
 $("km").textContent=c.api_key_mask?"(সেভ করা: "+c.api_key_mask+")":"(নেই)";
 $("pm").textContent=c.key_saved?"(সেভ করা আছে)":"(নেই)";}
async function save(){const d={};
 for(const k of ["base_url","api_key","account_id","instrument","private_key","dirs","entry","tf","be_on","auto_learn","strat","b_tf","b_sma_on","b_trail_on","b_sl_type"].concat(NUM))d[k]=$(k).value;
 try{const j=await post(d);setMsg(j.ok?"সেভ হয়েছে, নতুন সেটিংসে চলছে":("ত্রুটি: "+j.error));
 if(j.ok){$("api_key").value="";$("private_key").value="";loadSettings();load();}}catch(e){setMsg("সংযোগ ত্রুটি");}}
let TF="M5",VIS=80,CD=[],IDXT=null,SMA_ON=true,LV_ON=true,ZN_ON=true,LV=null,PAN=0,SP=null,MANUAL=false,YS=1,YOFF=0,RNG=1,TR=null,TL_ON=true;
const ZS=[30,50,80,120,200];
function p2(n){return String(n).padStart(2,"0");}
function fmtT(ts,full){const t=new Date(ts*1000);const hm=p2(t.getHours())+":"+p2(t.getMinutes());
 return (full||TF=="M15"||TF=="M30"||TF=="H1"||TF=="H4")?p2(t.getDate())+"/"+p2(t.getMonth()+1)+" "+hm:hm;}
const TFN={M1:"1 মিনিট",M5:"5 মিনিট",M15:"15 মিনিট",M30:"30 মিনিট",H1:"1 ঘণ্টা",H4:"4 ঘণ্টা"};
function tfMenu(e){e.stopPropagation();$("tfmenu").classList.toggle("hide");}
document.addEventListener("click",()=>$("tfmenu").classList.add("hide"));
document.addEventListener("keydown",e=>{if(e.key=="Escape")$("tfmenu").classList.add("hide");});
function setTF(t){TF=t;IDXT=null;CD=[];for(const b of document.querySelectorAll("#tfmenu button"))b.classList.toggle("on",b.dataset.tf==t);
 $("tfbtn").firstElementChild.textContent=TFN[t];$("tfmenu").classList.add("hide");draw();loadChart();}
function zoom(d){let i=ZS.indexOf(VIS);if(i<0)i=2;i=Math.max(0,Math.min(ZS.length-1,i-d));VIS=ZS[i];draw();}
function toggleZn(){ZN_ON=!ZN_ON;$("znb").classList.toggle("on",ZN_ON);draw();}
function toggleLv(){LV_ON=!LV_ON;$("lvb").classList.toggle("on",LV_ON);draw();}
function toggleSma(){SMA_ON=!SMA_ON;$("smab").classList.toggle("on",SMA_ON);draw();}
function toggleTl(){TL_ON=!TL_ON;$("tlb").classList.toggle("on",TL_ON);draw();}
function tlLv(){const a=[];if(!TR)return a;for(const p of TR.positions||[]){for(const v of [p.open,p.sl,p.tp])if(v!=null)a.push(v);}
 const q=TR.plan;if(q){for(const v of [q.entry,q.sl,q.tp])if(v!=null)a.push(v);}return a;}
async function loadChart(){
 const want=TF;
 try{const r=await fetch("/api/candles?tf="+want+"&n=300");const j=await r.json();
  if(want!=TF)return;
  if(j.error){$("cerr").textContent=j.error;return;}
  $("cerr").textContent="";const ol=CD.length;CD=j.candles;if(PAN>0&&ol&&CD.length>=ol)PAN+=CD.length-ol;LV=j.levels||null;TR=j.trade||null;SP=j.spread==null?null:j.spread;draw();}
 catch(e){$("cerr").textContent="চার্ট ডেটা আসেনি";}}
function draw(){
 const cv=$("cv"),dpr=window.devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;
 if(!W)return;cv.width=W*dpr;cv.height=H*dpr;
 const g=cv.getContext("2d");g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
 if(!CD.length){$("ro").textContent="ডেটা লোড হচ্ছে…";return;}
 const vis=Math.min(VIS,CD.length);PAN=Math.max(-Math.floor(vis/2),Math.min(PAN,CD.length-vis));const end=CD.length-PAN,endc=Math.min(end,CD.length),off=end-vis,s0=Math.max(0,off),lead=s0-off,d=CD.slice(s0,endc);
 const PR=54,PB=18,VH=40,top=6,pH=H-PB-VH-top-6,pW=W-PR;
 let hi=-1e18,lo=1e18,vmax=0;
 for(const c of d){hi=Math.max(hi,c[2]);lo=Math.min(lo,c[3]);vmax=Math.max(vmax,c[5]);}
 if(TL_ON&&!MANUAL){const cr=hi-lo;for(const v of tlLv()){if(v>hi&&v-hi<=cr*1.5)hi=v;else if(v<lo&&lo-v<=cr*1.5)lo=v;}}
 const pad=(hi-lo)*0.06||1;hi+=pad;lo-=pad;
 if(MANUAL){const c0=(hi+lo)/2+YOFF,h0=(hi-lo)/2*YS;hi=c0+h0;lo=c0-h0;}
 RNG=hi-lo;
 const y=v=>top+(hi-v)/(hi-lo)*pH,cw=pW/vis,x=i=>(i+0.5)*cw;
 g.font="11px sans-serif";g.textBaseline="middle";g.lineWidth=1;
 for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=y(v);g.strokeStyle="#232733";g.beginPath();g.moveTo(0,yy);g.lineTo(pW,yy);g.stroke();g.fillStyle="#8b90a0";g.fillText(v.toFixed(2),pW+4,yy);}
 const bw=Math.max(1,cw*0.7);
 d.forEach((c,i0)=>{const i=i0+lead;const col=c[4]>=c[1]?"#2ecc71":"#ff5c5c";g.strokeStyle=col;g.fillStyle=col;
  g.beginPath();g.moveTo(x(i),y(c[2]));g.lineTo(x(i),y(c[3]));g.stroke();
  const y1=y(Math.max(c[1],c[4])),y2=y(Math.min(c[1],c[4]));g.fillRect(x(i)-bw/2,y1,bw,Math.max(1,y2-y1));
  if(vmax>0){g.globalAlpha=0.35;const vh=c[5]/vmax*VH;g.fillRect(x(i)-bw/2,H-PB-vh,bw,vh);g.globalAlpha=1;}});
 if(SMA_ON&&CD.length>=50){g.save();g.beginPath();g.rect(0,0,pW,top+pH);g.clip();
  g.strokeStyle="#f5b942";g.lineWidth=1.3;g.beginPath();let s=0,st=false;
  for(let i=0;i<endc;i++){s+=CD[i][4];if(i>=50)s-=CD[i-50][4];
   if(i>=49&&i>=off){const yy=y(s/50),xx=x(i-off);if(!st){g.moveTo(xx,yy);st=true;}else g.lineTo(xx,yy);}}
  g.stroke();g.restore();g.lineWidth=1;}
 if(ZN_ON&&LV&&LV.poc&&LV.tol!=null){g.save();g.font="11px sans-serif";g.textAlign="right";g.textBaseline="middle";
  const T=LV.tol,S=LV.sma,zs=[];
  if(LV.dirs!="S"){zs.push(["BUY VAL",LV.val-T,LV.val+T,"buy"]);zs.push(["BUY POC",LV.poc,LV.poc+T,"buy"]);}
  if(LV.dirs!="L"){zs.push(["SELL VAH",LV.vah-T,LV.vah+T,"sell"]);zs.push(["SELL POC",LV.poc-T,LV.poc,"sell"]);}
  let k=0;
  for(const [nm,a0,b0,sd] of zs){
   let a1=a0,b1=b0;
   if(sd=="buy")a1=Math.max(a0,S);else b1=Math.min(b0,S);
   const col=sd=="buy"?"46,204,113":"255,92,92";
   const ok=b1>a1;
   if(ok){if(b1<lo||a1>hi){k++;continue;}
    const yt=y(Math.min(b1,hi)),yb=y(Math.max(a1,lo)),hh=Math.max(3,yb-yt);
    g.fillStyle="rgba("+col+",0.22)";g.fillRect(0,yt,pW,hh);
    g.strokeStyle="rgba("+col+",0.8)";g.lineWidth=1;g.strokeRect(0,yt,pW,hh);
    g.fillStyle="rgb("+col+")";g.fillText(nm+" এন্ট্রি জোন",pW-4,yt-6);}
   else{g.fillStyle="#8b90a0";g.fillText(nm+": SMA-র কারণে বন্ধ",pW-4,top+10+k*13);}
   k++;}
  g.restore();}
 if(LV_ON&&LV&&LV.poc){const defs=[["VAH",LV.vah,"#ff5c5c"],["POC",LV.poc,"#f5b942"],["VAL",LV.val,"#2ecc71"]];
  g.save();g.font="11px sans-serif";g.textBaseline="middle";
  for(const [nm,v,col] of defs){
   if(v==null)continue;
   if(v>=lo&&v<=hi){const yy=y(v);g.strokeStyle=col;g.lineWidth=1.2;g.setLineDash([6,4]);g.beginPath();g.moveTo(0,yy);g.lineTo(pW,yy);g.stroke();g.setLineDash([]);
    g.fillStyle=col;g.fillText(nm+" "+v.toFixed(2),4,yy-7);}
   else{const up=v>hi,yy=up?top+8:top+pH-8;g.fillStyle=col;g.fillText((up?"▲ ":"▼ ")+nm+" "+v.toFixed(2),4,yy+(nm=="VAH"?0:nm=="POC"?13:26)*(up?1:-1));}}
  g.restore();}
 if(TL_ON&&TR){g.save();g.font="11px sans-serif";g.textBaseline="middle";g.textAlign="right";
  const TFS={M1:60,M5:300,M15:900,M30:1800,H1:3600,H4:14400}[TF]||300,items=[];
  for(const p of TR.positions||[]){const sd=(p.side||"").toUpperCase();
   if(p.open!=null)items.push([p.open,sd+" "+(p.volume==null?"":p.volume)+(p.pnl==null?"":"  ≈"+(p.pnl>=0?"+":"")+p.pnl.toFixed(2)+"$"),"#4da3ff",false]);
   if(p.sl!=null)items.push([p.sl,"SL "+p.sl.toFixed(2),"#ff5c5c",false]);
   if(p.tp!=null)items.push([p.tp,"TP "+p.tp.toFixed(2),"#2ecc71",false]);
   if(p.sl==null&&p.tp==null&&p.open!=null)items.push([null,"",null,false]);}
  const q=TR.plan;
  if(q){items.push([q.entry,"প্ল্যান "+q.side.toUpperCase()+" এন্ট্রি "+q.entry.toFixed(2),"#4da3ff",true]);items.push([q.sl,"প্ল্যান SL "+q.sl.toFixed(2),"#ff5c5c",true]);
   if(q.tp!=null)items.push([q.tp,"প্ল্যান TP "+q.tp.toFixed(2),"#2ecc71",true]);
   else{const m="প্ল্যান TP নেই, ট্রেড বাদ";const tw=g.measureText(m).width;g.fillStyle="rgba(15,17,21,0.85)";g.fillRect(pW-tw-8,top+pH-24,tw+8,16);g.fillStyle="#8b90a0";g.fillText(m,pW-4,top+pH-16);}}
  let up=0,dn=0;
  for(const [v,txt,col,dash] of items){if(v==null)continue;let yy;
   if(v>=lo&&v<=hi){yy=y(v);g.strokeStyle=col;g.lineWidth=dash?1.2:1.7;g.setLineDash(dash?[3,3]:[]);g.beginPath();g.moveTo(0,yy);g.lineTo(pW,yy);g.stroke();g.setLineDash([]);g.lineWidth=1;}
   else{const upv=v>hi;yy=upv?top+8+up*14:top+pH-8-dn*14;if(upv)up++;else dn++;}
   const label=(v>hi?"▲ ":v<lo?"▼ ":"")+txt,tw=g.measureText(label).width;
   g.fillStyle="rgba(15,17,21,0.85)";g.fillRect(pW-tw-8,yy-8,tw+8,16);g.fillStyle=col;g.fillText(label,pW-4,yy);}
  for(const p of TR.positions||[]){if(p.t==null||p.open==null)continue;const ix=CD.findIndex(c=>c[0]<=p.t&&p.t<c[0]+TFS);
   if(ix>=off&&ix<end&&p.open>=lo&&p.open<=hi){const xx=x(ix-off),yy=y(p.open),b=p.side=="buy";
    g.fillStyle=b?"#2ecc71":"#ff5c5c";g.beginPath();if(b){g.moveTo(xx,yy+2);g.lineTo(xx-5,yy+11);g.lineTo(xx+5,yy+11);}else{g.moveTo(xx,yy-2);g.lineTo(xx-5,yy-11);g.lineTo(xx+5,yy-11);}g.fill();}}
  g.restore();}
 const L=CD[CD.length-1],ly=y(L[4]),lc=L[4]>=L[1]?"#2ecc71":"#ff5c5c";
 if(L[4]>=lo&&L[4]<=hi){g.setLineDash([4,3]);g.strokeStyle="#8b90a0";g.beginPath();g.moveTo(0,ly);g.lineTo(pW,ly);g.stroke();g.setLineDash([]);
 g.fillStyle=lc;g.fillRect(pW,ly-8,PR,16);g.fillStyle="#0f1115";g.fillText(L[4].toFixed(2),pW+3,ly);
  if(SP!=null){const av=L[4]+SP,ay=y(av),ly2=Math.min(ay,ly-16);
   g.setLineDash([2,3]);g.strokeStyle="#4da3ff";g.beginPath();g.moveTo(0,ay);g.lineTo(pW,ay);g.stroke();g.setLineDash([]);
   g.fillStyle="#4da3ff";g.fillRect(pW,ly2-8,PR,16);g.fillStyle="#0f1115";g.fillText(av.toFixed(2),pW+3,ly2);}}
 const step=Math.max(1,Math.ceil(72/cw));g.fillStyle="#8b90a0";g.textBaseline="alphabetic";g.textAlign="center";
 for(let i=Math.min(vis-1,CD.length-1-off);i>=lead;i-=step){const xx=x(i);if(xx>20&&xx<pW-20)g.fillText(fmtT(CD[off+i][0]),xx,H-4);}
 g.textAlign="left";
 let ci=IDXT==null?-1:CD.findIndex(c=>c[0]==IDXT);
 if(ci>=0&&ci>=off&&ci<endc){g.strokeStyle="#8b90a0";g.setLineDash([3,3]);g.beginPath();g.moveTo(x(ci-off),top);g.lineTo(x(ci-off),H-PB);g.stroke();g.setLineDash([]);}
 else ci=endc-1;
 const c=CD[ci];
 $("ro").textContent=fmtT(c[0],true)+"  O "+c[1].toFixed(2)+"  H "+c[2].toFixed(2)+"  L "+c[3].toFixed(2)+"  C "+c[4].toFixed(2)+"  V "+c[5];
}
function pick(e){const cv=$("cv"),r=cv.getBoundingClientRect();if(!CD.length)return;
 const vis=Math.min(VIS,CD.length),off=CD.length-Math.max(-Math.floor(vis/2),Math.min(PAN,CD.length-vis))-vis,cw=(r.width-54)/vis;
 let i=Math.floor((e.clientX-r.left)/cw);i=Math.max(0,Math.min(vis-1,i));if(off+i<0||!CD[off+i])return;IDXT=CD[off+i][0];draw();}
const PT=new Map();let PD0=0,PV0=80,PINCH=false,DRAG=false,SX=0,SY=0,SPAN=0,ZONE="plot",YS0=1,YOFF0=0,RNG0=1;
function setVis(v){const mx=Math.max(15,Math.min(400,CD.length||400));VIS=Math.max(15,Math.min(mx,Math.round(v)));draw();}
function pdist(){const a=[...PT.values()];return Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y)||1;}
function setYS(v){MANUAL=true;YS=Math.max(0.05,Math.min(20,v));draw();}
$("cv").addEventListener("pointerdown",e=>{PT.set(e.pointerId,{x:e.clientX,y:e.clientY});
 try{$("cv").setPointerCapture(e.pointerId);}catch(_){}
 if(PT.size==2){PINCH=true;PD0=pdist();PV0=Math.min(VIS,CD.length||VIS);}
 else if(PT.size==1){PINCH=false;DRAG=false;SX=e.clientX;SY=e.clientY;SPAN=PAN;YS0=YS;YOFF0=YOFF;RNG0=RNG;
  const r=$("cv").getBoundingClientRect();
  ZONE=(e.clientX-r.left>r.width-54)?"y":(e.clientY-r.top>r.height-30)?"x":"plot";}});
$("cv").addEventListener("pointermove",e=>{if(!PT.has(e.pointerId))return;PT.set(e.pointerId,{x:e.clientX,y:e.clientY});
 if(PT.size==2){setVis(PV0*PD0/pdist());return;}
 if(PINCH||!CD.length)return;
 const dx=e.clientX-SX,dy=e.clientY-SY;
 if(!DRAG&&Math.hypot(dx,dy)>6)DRAG=true;
 if(!DRAG)return;
 if(ZONE=="y"){setYS(YS0*Math.exp(dy/150));return;}
 const r=$("cv").getBoundingClientRect(),vis=Math.min(VIS,CD.length),cw=(r.width-54)/vis;
 PAN=Math.max(-Math.floor(vis/2),Math.min(CD.length-vis,SPAN+Math.round(dx/cw)));
 if(ZONE=="plot"&&MANUAL){const pH=Math.max(1,r.height-70);YOFF=YOFF0+dy/pH*RNG0;}
 draw();});
function pend(e){const had=PT.has(e.pointerId);PT.delete(e.pointerId);
 if(had&&PT.size==0){if(!PINCH&&!DRAG&&ZONE=="plot"&&e.type=="pointerup")pick(e);PINCH=false;DRAG=false;}
 else if(PT.size==1)PINCH=true;}
for(const ev of ["pointerup","pointercancel"])$("cv").addEventListener(ev,pend);
$("cv").addEventListener("wheel",e=>{e.preventDefault();const r=$("cv").getBoundingClientRect();
 if(e.clientX-r.left>r.width-54)setYS(YS*(e.deltaY>0?1.15:0.87));
 else setVis(Math.min(VIS,CD.length||VIS)*(e.deltaY>0?1.15:0.87));},{passive:false});
window.addEventListener("resize",draw);
setInterval(()=>{if(!$("p2").classList.contains("hide"))loadChart();},4000);
load();setInterval(load,5000);loadSettings();
</script></body></html>'''


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _ok(self):
        hosts = (f"localhost:{PORT}", f"127.0.0.1:{PORT}")
        if self.headers.get("Host", "") not in hosts:
            return False
        o = self.headers.get("Origin")
        return not o or o in ["http://" + h for h in hosts]

    def do_GET(self):
        if not self._ok():
            return self._send(403, '{"error":"forbidden"}')
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, PAGE, "text/html")
        elif u.path == "/api/status":
            with LOCK:
                self._send(200, json.dumps(STATE))
        elif u.path == "/api/settings":
            self._send(200, json.dumps(public_cfg()))
        elif u.path == "/api/candles":
            try:
                q = parse_qs(u.query)
                tf = q.get("tf", ["M5"])[0]
                if tf not in TF_MIN:
                    raise ValueError("টাইমফ্রেম " + ", ".join(TF_MIN))
                n = max(30, min(300, int(q.get("n", ["250"])[0])))
                with LOCK:
                    tr_view = STATE.get("trade")
                self._send(200, json.dumps(dict(chart_data(tf, n), levels=LEVELS, spread=STATE.get("spread"), trade=tr_view)))
            except Exception as e:
                self._send(200, json.dumps({"error": str(e)[:200]}, ensure_ascii=False))
        else:
            self._send(404, '{"error":"not found"}')

    def do_POST(self):
        if not self._ok():
            return self._send(403, '{"ok":false,"error":"forbidden"}')
        if self.path not in ("/api/settings", "/api/learn/run", "/api/learn/apply"):
            return self._send(404, '{"ok":false,"error":"not found"}')
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 20000:
                raise ValueError("অনেক বড় রিকুয়েস্ট")
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/learn/run":
                with LOCK:
                    cur = dict(CFG)
                maybe_learn(cur, force=True)
                if FIB is not None:
                    maybe_learn_b(cur, force=True, wait=True)
            elif self.path == "/api/learn/apply":
                with ML:
                    ch = dict(META.get("pending") or {}); ch.update(META.get("b_pending") or {})
                if not ch:
                    raise ValueError("প্রয়োগের মতো কোনো সাজেশন নেই")
                apply_changes(ch, "হাতে প্রয়োগ", False)
            else:
                apply_settings(body)
            self._send(200, '{"ok":true}')
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)[:200]}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        sys.exit(f"পোর্ট {PORT} ব্যস্ত। আগের প্রোগ্রাম বন্ধ করো (pkill -f exness_all.py) অথবা DASH_PORT=8081 দিয়ে চালাও")
    load_learn_state()
    sh_load()
    threading.Thread(target=worker, daemon=True).start()
    print(f"চালু: http://localhost:{PORT}  (বন্ধ করতে Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nবন্ধ")
