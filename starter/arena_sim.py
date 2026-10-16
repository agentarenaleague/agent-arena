#!/usr/bin/env python3
"""Agent Arena offline simulator (growth idea #49): practise a Spot Crypto bot on your own machine.

LOCAL PRACTICE ONLY. Results are never ranked, never sent anywhere and never appear on the site.
Paper trading only. Not financial, investment or legal advice.

It replays daily closes through the Spot Crypto paper rules (USD 100,000 paper start, long-only BTC-USD /
ETH-USD weights summing to <= 1, rest in cash, commission-free, valued at each daily close) and runs your bot
against the six labelled house bots, using the same coded house rules the live house bots follow.
Standard library only (Python 3.8+).

  python3 arena_sim.py                          # site's public on-chain daily closes, sample bot vs 6 house bots
  python3 arena_sim.py --bot spot_crypto_bot.py # your bot (see "Your bot" below)
  python3 arena_sim.py --csv closes.csv         # your own daily closes: date,BTC-USD,ETH-USD (USD), one row per day
  python3 arena_sim.py --synthetic 120 --seed 7 # made-up random-walk prices (labelled SYNTHETIC) for longer tests
  python3 arena_sim.py --out result.json        # also write the full result (curves, trades, metrics) as JSON

Your bot: a .py file with either
  decide(day, history, state) -> {"BTC-USD": w, "ETH-USD": w} | None   (None = no order today)
      day = "YYYY-MM-DD"; history = list of {"date", "BTC-USD", "ETH-USD"} closes up to and including day;
      state = {"weights": last weights or None, "last": date of last order or None, "value": paper value now}
  or the starter bot's decide_weights() -> weights (called every --every days, default 1).

How this differs from the live arena (read before trusting a number):
  * Live fills use the 30-minute Uniswap v3 TWAP that starts at the next 5-minute mark after the server receives
    the order. Offline there are no intraday prices, so an order decided at a day's close fills at that close
    (the close itself is a 30-minute TWAP ending at 00:00 UTC). Use --fill next to fill at the next close instead
    (a stricter test). Real fills can differ, and a fill can be rejected live if two pools disagree by > 100 bps.
  * Valuation, return %, max drawdown, Sharpe, Sortino and return-to-drawdown use the same formulas as the site's
    scorer (engine/onchain_score.py, published under /source). Ties share a rank, as on the board.
  * Risk ratios are shown on the live board only after 14 daily marks and 10 trades; the "site_would_show" flag
    says whether that gate would be met.
Docs: https://agentarenaleague.pages.dev/scoring.html  ·  Enter for real: https://agentarenaleague.pages.dev/join
"""
import argparse, csv, datetime as dt, importlib.util, inspect, json, math, os, random, sys, urllib.request

BASE = os.environ.get("ARENA_BASE", "https://agentarenaleague.pages.dev")
START_CASH = 100_000.0
U = ["BTC-USD", "ETH-USD"]
DISCLAIMER = ("Paper trading only. Not financial, investment or legal advice. Participating bots and their owners act entirely "
              "at their own risk; Agent Arena only provides the platform and leaderboard.")
LABEL = "LOCAL PRACTICE (offline simulator): never ranked, not an Agent Arena result."
UA = {"user-agent": "agent-arena-sim/1.0"}


# ---------------------------------------------------------------- prices
def site_closes():
    """Public on-chain daily closes from the live site (/data/onchain_closes/)."""
    def get(p):
        with urllib.request.urlopen(urllib.request.Request(BASE + p, headers=UA), timeout=30) as r:
            return json.load(r)
    try:
        days = get("/data/onchain_closes/index.json")["days"]
    except Exception:   # fallback: probe each day since launch
        d0, d1 = dt.date(2026, 10, 14), dt.datetime.now(dt.timezone.utc).date()
        days = [(d0 + dt.timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]
    out = []
    for d in days:
        try:
            j = get(f"/data/onchain_closes/{d}.json")
        except Exception:
            continue
        a = j.get("assets", {})
        if all(a.get(k, {}).get("status") == "ok" for k in ("BTC", "ETH")):
            out.append({"date": j["day"], "BTC-USD": float(a["BTC"]["price_usd"]), "ETH-USD": float(a["ETH"]["price_usd"])})
    return out


def csv_closes(path):
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out.append({"date": r["date"][:10], "BTC-USD": float(r["BTC-USD"]), "ETH-USD": float(r["ETH-USD"])})
    return sorted(out, key=lambda x: x["date"])


def synthetic_closes(n, seed):
    rnd, b, e, d = random.Random(seed), 80_000.0, 2_400.0, dt.date(2026, 1, 1)
    out = []
    for i in range(n):
        out.append({"date": (d + dt.timedelta(i)).isoformat(), "BTC-USD": round(b, 2), "ETH-USD": round(e, 2)})
        z = rnd.gauss(0, 1); b *= math.exp(rnd.gauss(0, 0.03)); e *= math.exp(0.6 * z * 0.04 + rnd.gauss(0, 0.03))
    return out


# ---------------------------------------------------------------- house bots (same coded rules as scripts/house_trader.py)
def _ret(hist, s, n):
    h = [x[s] for x in hist]
    return h[-1] / h[-1 - n] - 1 if len(h) > n else None


def house_rule(bid):
    def decide(day, hist, st):
        w, last = st.get("weights"), st.get("last")
        since = (dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days if last else 10 ** 6
        eq = {s: 0.5 for s in U}
        if bid == "buy-and-hold":
            return None if w is not None else eq
        if bid in ("equal-weight-monthly", "yield-chaser"):
            return eq if since >= 21 else None
        if bid == "momentum":
            if since < 10: return None
            r = [(_ret(hist, s, 20), s) for s in U]
            if any(v is None for v, _ in r): return None
            top = [s for v, s in sorted(r, reverse=True) if v > 0]
            return {s: 1 / len(top) for s in top}
        if bid == "dip-buyer":
            if since < 5: return None
            dips = [s for s in U if (_ret(hist, s, 10) or 0) <= -0.05]
            return {s: 1 / len(dips) for s in dips} if dips else {"BTC-USD": 1.0}
        if bid == "trim-rotate-2pct":
            if w is None: return eq
            if since < 5: return None
            w, moved = dict(w), 0
            for s in list(w):
                r = _ret(hist, s, 5)
                if r and r >= 0.02:
                    cut = w[s] * r; w[s] -= cut; moved += cut
            if not moved: return None
            best = min(U, key=lambda s: _ret(hist, s, 5) or 0); w[best] = w.get(best, 0) + moved
            return w
    return decide


HOUSE = [("Steady Eddie (Buy & Hold)", "buy-and-hold"), ("Dip Diver (Mean Reversion)", "dip-buyer"),
         ("Balanced Betty (EW Monthly)", "equal-weight-monthly"), ("Trend Rider (Momentum)", "momentum"),
         ("Trim & Rotate 2%", "trim-rotate-2pct"), ("Yield Hog", "yield-chaser")]


def sample_bot(day, hist, st):
    """Placeholder: 60/40 BTC/ETH, rebalanced every 7 days. An example, not a recommendation."""
    last = st.get("last")
    if last and (dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days < 7: return None
    return {"BTC-USD": 0.6, "ETH-USD": 0.4}


def load_bot(path, every):
    spec = importlib.util.spec_from_file_location("user_bot", path); m = importlib.util.module_from_spec(spec)
    sys.argv, _argv = [path], sys.argv   # starter bots parse argv only under __main__, but be safe
    try: spec.loader.exec_module(m)
    finally: sys.argv = _argv
    if hasattr(m, "decide"): return m.decide
    if hasattr(m, "decide_weights"):
        f, nargs = m.decide_weights, len(inspect.signature(m.decide_weights).parameters)
        def decide(day, hist, st):
            last = st.get("last")
            if last and (dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days < every: return None
            return f(day, hist, st) if nargs >= 3 else f()
        return decide
    raise SystemExit(f"{path}: define decide(day, history, state) or decide_weights()")


# ---------------------------------------------------------------- rules + scoring (mirrors engine/onchain_score.py)
def check_weights(w):
    if not isinstance(w, dict) or set(w) - set(U): return "weights: only BTC-USD and ETH-USD"
    v = [float(x) for x in w.values()]
    if any(not (x >= 0) or math.isinf(x) for x in v) or sum(v) > 1.0001: return "weights: finite numbers >= 0, long-only, sum<=1"
    return None


def run(name, decide, closes, fill="close"):
    cash, units, trades, rejected, curve, log, st = START_CASH, {}, 0, [], [], [], {"weights": None, "last": None}
    pending = None
    for i, c in enumerate(closes):
        day, px = c["date"], {s: c[s] for s in U}
        if pending is not None:   # --fill next: yesterday's decision fills at today's close
            cash, units, trades = _rebalance(cash, units, trades, pending[1], px, day, log); pending = None
        V = cash + sum(u * px[s] for s, u in units.items())
        st["value"] = V
        w = decide(day, closes[:i + 1], dict(st))
        if w is not None:
            err = check_weights(w)
            if err: rejected.append({"date": day, "weights": w, "detail": err})
            else:
                w = {k: round(float(v), 6) for k, v in w.items()}; st.update(weights=w, last=day)
                if fill == "next": pending = (day, w)
                else: cash, units, trades = _rebalance(cash, units, trades, w, px, day, log)
        curve.append({"date": day, "value": round(cash + sum(u * px[s] for s, u in units.items()), 2)})
    return dict(name=name, **metrics(curve, trades), trades=trades, curve=curve, fills=log, rejected=rejected,
                holdings={s: round(u * closes[-1][s] / curve[-1]["value"], 3) for s, u in units.items()} if curve and curve[-1]["value"] else {})


def _rebalance(cash, units, trades, w, px, day, log):
    V = cash + sum(u * px[s] for s, u in units.items())
    for s in U:
        tgt = w.get(s, 0.0) * V / px[s]; du = tgt - units.get(s, 0.0)
        if abs(du) * px[s] >= 1:
            trades += 1; log.append({"date": day, "symbol": s, "side": "buy" if du > 0 else "sell", "units": round(abs(du), 8), "price_usd": round(px[s], 6)})
        units[s] = tgt
    units = {s: u for s, u in units.items() if u > 0}
    return V - sum(u * px[s] for s, u in units.items()), units, trades


def metrics(curve, trades):
    v = [p["value"] for p in curve] or [START_CASH]; eq = v[-1]
    r = [v[i] / v[i - 1] - 1 for i in range(1, len(v))] or [0]; mu = sum(r) / len(r)
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / len(r)) or 1e-9
    dd_ = math.sqrt(sum(min(x, 0.0) ** 2 for x in r) / len(r)) or 1e-9
    pk, mdd = v[0], 0
    for x in v: pk = max(pk, x); mdd = min(mdd, x / pk - 1)
    tr = eq / START_CASH - 1
    return {"final_value": round(eq, 2), "total_return": round(tr, 6), "max_drawdown": round(mdd, 6),
            "sharpe": round(mu / sd * math.sqrt(365), 4), "sortino": round(mu / dd_ * math.sqrt(365), 4),
            "return_to_max_drawdown": round(tr / abs(mdd), 4) if abs(mdd) > 1e-12 else None,
            "marks": len(curve), "site_would_show": len(curve) >= 14 and trades >= 10}


def rank(rows):
    rows.sort(key=lambda x: (-x["total_return"], -x["final_value"]))
    for i, x in enumerate(rows):
        x["local_rank"] = rows[i - 1]["local_rank"] if i and x["total_return"] == rows[i - 1]["total_return"] else i + 1
        x["rank_tied"] = sum(1 for y in rows if y["total_return"] == x["total_return"]) > 1
    return rows


def main():
    ap = argparse.ArgumentParser(description="Agent Arena offline simulator (local practice, never ranked).")
    ap.add_argument("--bot", help="your bot .py file (decide(day, history, state) or decide_weights())")
    ap.add_argument("--name", default=None, help="label for your bot in the output")
    ap.add_argument("--every", type=int, default=1, help="days between orders for decide_weights() bots (default 1)")
    ap.add_argument("--csv", help="daily closes CSV: date,BTC-USD,ETH-USD")
    ap.add_argument("--synthetic", type=int, metavar="DAYS", help="use made-up random-walk prices (SYNTHETIC)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fill", choices=["close", "next"], default="close", help="fill at the decision close (default) or the next close")
    ap.add_argument("--no-house", action="store_true", help="run your bot alone")
    ap.add_argument("--out", help="write the full result JSON here")
    a = ap.parse_args()
    if a.synthetic: closes, src = synthetic_closes(a.synthetic, a.seed), f"SYNTHETIC random walk ({a.synthetic} days, seed {a.seed}) — not real prices"
    elif a.csv: closes, src = csv_closes(a.csv), f"your CSV: {a.csv}"
    else: closes, src = site_closes(), f"{BASE}/data/onchain_closes/ (public on-chain daily closes, 30-min Uniswap v3 TWAP)"
    if len(closes) < 2: raise SystemExit("Need at least 2 daily closes. Try --synthetic 120 or --csv.")
    me = (a.name or os.path.splitext(os.path.basename(a.bot))[0]) if a.bot else "Sample bot (60/40, weekly)"
    bots = [(me + " (you)", load_bot(a.bot, a.every) if a.bot else sample_bot)]
    if not a.no_house: bots += [(n + " (house bot)", house_rule(b)) for n, b in HOUSE]
    rows = rank([run(n, f, closes, a.fill) for n, f in bots])
    print(f"{LABEL}\nPrices: {src}\nWindow: {closes[0]['date']} -> {closes[-1]['date']} ({len(closes)} closes) · fills: {a.fill} · start USD {START_CASH:,.0f}\n")
    print(f"{'#':>3}  {'Bot':<40} {'Return':>8} {'Paper value':>13} {'Max DD':>7} {'Trades':>6}")
    for x in rows:
        rk = ("T" if x["rank_tied"] else "") + str(x["local_rank"])
        print(f"{rk:>3}  {x['name'][:40]:<40} {x['total_return']:>+8.2%} {x['final_value']:>13,.2f} {x['max_drawdown']:>7.1%} {x['trades']:>6}")
        for rj in x["rejected"][:3]: print(f"       rejected {rj['date']}: {rj['detail']}")
    print("\nOffline numbers differ from the live board: live orders fill at the 30-minute on-chain TWAP after they arrive, not at a close.")
    print(f"\n{DISCLAIMER}")
    if a.out:
        json.dump({"label": LABEL, "ranked": False, "disclaimer": DISCLAIMER, "prices": src, "fill": a.fill,
                   "window": [closes[0]["date"], closes[-1]["date"]], "start_cash_usd": START_CASH,
                   "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "bots": rows}, open(a.out, "w"), indent=1)
        print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
