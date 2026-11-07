"""[IMBA] ALGO (Fibonacci channel) - strategy engine, v2.

Signal : Fibonacci channel over the last N = sensitivity*10 bars (use 1H bars).
         Long  when close >= fib0.236 (and >= fib0.5) and not already in long trend
         Short when close <= fib0.786 (and <= fib0.5) and not already in short trend
         Trend flips exactly like the Pine indicator. A SMA filter (default SMA 50)
         can veto a signal: longs only above the SMA, shorts only below it.
         A trend flip always closes the open trade; the opposite trade is NOT
         opened unless the filter lets that new signal through.
Entry  : close of the signal bar.
SL     : fib0.786 (long) / fib0.236 (short), or fixed % of entry, +/- sl_pct.
TPs    : entry +/- 1,2,3,4 %, closing 40/30/20/10 % of the position.
BE     : TP2 hit (or be_target TP touched) -> stop moves to entry.
Trail  : once profit reaches trail_start_pct (1 %), SL follows price at a fixed
         trail_dist_usd behind the best price, never below entry, never backwards.
         Trailing replaces BE once it is active.

pine_mode=True  : bar logic identical to the Pine script (TPs checked before the
                  stop, BE exit needs a bearish bar for longs / bullish for shorts).
                  Use it for backtests on candles.
pine_mode=False : live logic. Feed ticks (lo=hi=price). Stop is checked first and
                  a touch of the stop closes the trade at once.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import math


@dataclass
class Params:
    sensitivity: float = 18.0
    tp_pct: tuple = (1.0, 2.0, 3.0, 4.0)
    tp_fix: tuple = (40.0, 30.0, 20.0, 10.0)
    sl_pct: float = 0.0
    fixed_stop: bool = False
    be_target: str = "1"            # "WITHOUT", "1", "2", "3"
    tick: float = 0.01
    start_ts: float | None = None   # ignore bars before this unix time (seconds)
    sma_enabled: bool = True
    sma_period: int = 50
    trail_enabled: bool = True
    trail_start_pct: float = 1.0    # profit % at which trailing starts
    trail_dist_usd: float = 8.0     # fixed distance behind price, in price units
    pine_mode: bool = True

    @property
    def lookback(self) -> int:
        return int(self.sensitivity * 10 + 1e-9)


def round_tick(x: float, tick: float) -> float:
    return round(math.floor(x / tick + 0.5) * tick, 10)


@dataclass
class Signal:
    side: str            # "long" | "short"
    ts: float
    entry: float
    sl: float
    tps: tuple
    fib236: float
    fib5: float
    fib786: float
    sma: float | None = None


class SignalEngine:
    """Feed closed bars in order. on_bar returns a Signal when a trade may open.
    `self.flip` is "long"/"short" on every bar where the trend flipped (even if the
    SMA filter blocked the signal), otherwise None: the caller closes any open
    trade whenever flip is set."""

    def __init__(self, p: Params):
        self.p = p
        self.highs: deque = deque(maxlen=p.lookback)
        self.lows: deque = deque(maxlen=p.lookback)
        self.closes: deque = deque(maxlen=max(p.sma_period, 1))
        self.is_long = False
        self.is_short = False
        self.flip: str | None = None

    def on_bar(self, ts: float, o: float, h: float, l: float, c: float) -> Signal | None:
        p = self.p
        self.flip = None
        self.highs.append(h)
        self.lows.append(l)
        self.closes.append(c)
        if len(self.highs) < p.lookback:
            return None
        if p.start_ts is not None and ts < p.start_ts:
            return None
        hi, lo = max(self.highs), min(self.lows)
        rng = hi - lo
        f236, f5, f786 = hi - rng * 0.236, hi - rng * 0.5, hi - rng * 0.786
        can_long = c >= f5 and c >= f236 and not self.is_long
        can_short = c <= f5 and c <= f786 and not self.is_short
        if can_long:
            side = "long"
            self.is_long, self.is_short = True, False
        elif can_short:
            side = "short"
            self.is_short, self.is_long = True, False
        else:
            return None
        self.flip = side
        sma = None
        if p.sma_enabled:
            if len(self.closes) < p.sma_period:
                return None
            sma = sum(self.closes) / len(self.closes)
            if (side == "long" and not c > sma) or (side == "short" and not c < sma):
                return None
        t, sgn = p.tick, (1 if side == "long" else -1)
        if p.fixed_stop:
            sl = c * (1 - sgn * p.sl_pct / 100)
        else:
            base = f786 if side == "long" else f236
            sl = base * (1 - sgn * p.sl_pct / 100)
        tps = tuple(round_tick(c * (1 + sgn * x / 100), t) for x in p.tp_pct)
        return Signal(side, ts, c, round_tick(sl, t), tps, f236, f5, f786, sma)


@dataclass
class Event:
    kind: str       # "TP" | "SL" | "BE" | "TRAIL" | "REVERSAL"
    n: int          # TP number (0 for others)
    price: float
    frac: float     # fraction of the ORIGINAL position closed by this event


class Trade:
    """Position manager shared by the backtester and the live bot.

    step(lo, hi, o=None, c=None) takes the price range since the last call, in the
    series that triggers exits (bid for longs, ask for shorts). o/c (bar open and
    close) are only needed in pine_mode, for the BE bar-colour rule.
    stop_price() is the stop the exchange order should currently carry."""

    def __init__(self, sig: Signal, p: Params):
        self.sig, self.p = sig, p
        self.long = sig.side == "long"
        self.entry, self.sl, self.tps = sig.entry, sig.sl, sig.tps
        self.fix = [x / 100 for x in p.tp_fix]
        self.hit = [False] * 4
        self.left = 1.0
        self.be = False
        self.closed = False
        be = {"1": 0, "2": 1, "3": 2}.get(p.be_target)
        self.be_price = self.tps[be] if be is not None else self.tps[3]
        sgn = 1 if self.long else -1
        self.trail_start = round_tick(self.entry * (1 + sgn * p.trail_start_pct / 100), p.tick)
        self.best = self.entry
        self.trail_on = False
        self.trail_stop = self.entry

    def _reached(self, lo, hi, level):
        return hi >= level if self.long else lo <= level

    def _touched(self, lo, hi, stop):
        return lo <= stop if self.long else hi >= stop

    def _stop(self):
        if self.trail_on:
            return self.trail_stop, "TRAIL"
        if self.be:
            return self.entry, "BE"
        return self.sl, "SL"

    def stop_price(self) -> float:
        return self._stop()[0]

    def _update_trail(self, lo, hi):
        p = self.p
        if not p.trail_enabled:
            return
        self.best = max(self.best, hi) if self.long else min(self.best, lo)
        if not self.trail_on and self._reached(lo, hi, self.trail_start):
            self.trail_on, self.be = True, True
        if self.trail_on:
            d = p.trail_dist_usd
            cand = self.best - d if self.long else self.best + d
            cand = round_tick(cand, p.tick)
            if self.long:
                self.trail_stop = max(self.trail_stop, cand, self.entry)
            else:
                self.trail_stop = min(self.trail_stop, cand, self.entry)

    def _close(self, kind, price, ev):
        ev.append(Event(kind, 0, price, self.left))
        self.left, self.closed = 0.0, True

    def step(self, lo: float, hi: float, o: float | None = None, c: float | None = None) -> list[Event]:
        ev: list[Event] = []
        if self.closed:
            return ev
        pine = self.p.pine_mode
        stop0, kind0 = self._stop()
        if not pine and self._touched(lo, hi, stop0):
            self._close(kind0, stop0, ev)
            return ev
        for i in range(4):
            if self.hit[i] or not self._reached(lo, hi, self.tps[i]):
                continue
            self.hit[i] = True
            frac = self.left if i == 3 else min(self.fix[i], self.left)
            self.left -= frac
            ev.append(Event("TP", i + 1, self.tps[i], frac))
            if i == 1:
                self.be = True
        if self._reached(lo, hi, self.be_price):
            self.be = True
        if self.left <= 1e-9:
            self.left, self.closed = 0.0, True
            return ev
        if pine:
            if kind0 == "TRAIL":
                if self._touched(lo, hi, stop0):
                    self._close("TRAIL", stop0, ev)
                    return ev
            elif self.be:
                bearish = (c is None or o is None) or (c < o if self.long else c > o)
                if self._touched(lo, hi, self.entry) and bearish:
                    self._close("BE", self.entry, ev)
                    return ev
            elif self._touched(lo, hi, self.sl):
                self._close("SL", self.sl, ev)
                return ev
        self._update_trail(lo, hi)
        return ev

    def close_all(self, price: float, kind: str = "REVERSAL") -> list[Event]:
        if self.closed:
            return []
        ev: list[Event] = []
        self._close(kind, price, ev)
        return ev


def run_backtest(bars, p: Params):
    """bars: iterable of (ts, open, high, low, close). Returns a list of trades as
    dicts {side, ts, entry, events, r_pct} where r_pct is the price-move % summed
    over the closed fractions (no risk sizing)."""
    eng, trade, cur, out = SignalEngine(p), None, None, []

    def finish():
        nonlocal trade, cur
        if cur is not None:
            sgn = 1 if cur["side"] == "long" else -1
            cur["r_pct"] = sum(sgn * (e.price / cur["entry"] - 1) * 100 * e.frac for e in cur["events"])
            out.append(cur)
        trade, cur = None, None

    for ts, o, h, l, c in bars:
        if trade is not None and not trade.closed:
            cur["events"] += trade.step(l, h, o, c)
            if trade.closed:
                finish()
        sig = eng.on_bar(ts, o, h, l, c)
        if eng.flip and trade is not None and not trade.closed:
            cur["events"] += trade.close_all(c)
            finish()
        if sig is not None and trade is None:
            trade = Trade(sig, p)
            cur = {"side": sig.side, "ts": ts, "entry": sig.entry, "events": []}
    return out


def backtest_r(bars, p: Params, spreads=None):
    """bars: list of (ts, open, high, low, close). Returns finished trades as dicts
    {side, ts, entry, sl, held, R}; R = profit / distance to SL, entry-bar spread subtracted.
    Trades still open at the end are not counted."""
    eng, trade, cur, out = SignalEngine(p), None, None, []

    def done():
        nonlocal trade, cur
        sg = 1 if cur["side"] == "long" else -1
        mv = sum(sg * (e.price - cur["entry"]) * e.frac for e in cur["ev"])
        mv -= (spreads or {}).get(cur["ts"], 0.0)
        dist = abs(cur["entry"] - cur["sl"])
        cur["R"] = mv / dist if dist > 0 else 0.0
        del cur["ev"]
        out.append(cur)
        trade = cur = None

    for ts, o, h, l, c in bars:
        if trade is not None and not trade.closed:
            cur["ev"] += trade.step(l, h, o, c)
            cur["held"] += 1
            if trade.closed:
                done()
        sig = eng.on_bar(ts, o, h, l, c)
        if eng.flip and trade is not None and not trade.closed:
            cur["ev"] += trade.close_all(c)
            done()
        if sig is not None and trade is None:
            trade = Trade(sig, p)
            cur = {"side": sig.side, "ts": ts, "entry": sig.entry, "sl": sig.sl, "held": 0, "ev": []}
    return out


def trade_stats(rs):
    """n, win % with 95 % Wilson interval, average R with 95 % margin, total R."""
    n = len(rs)
    if n == 0:
        return None
    pw = sum(1 for r in rs if r > 0) / n
    z = 1.96
    den = 1 + z * z / n
    centre = (pw + z * z / (2 * n)) / den
    half = z * math.sqrt(pw * (1 - pw) / n + z * z / (4 * n * n)) / den
    avg = sum(rs) / n
    sd = math.sqrt(sum((r - avg) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "win": 100 * pw, "lo": 100 * (centre - half), "hi": 100 * (centre + half),
            "avg": avg, "se95": z * sd / math.sqrt(n), "tot": sum(rs)}
