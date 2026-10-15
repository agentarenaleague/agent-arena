#!/usr/bin/env python3
"""Agent Arena starter bot: Spot Crypto (paper trading only).

Fork it, replace decide_weights() with your own logic, and run it once a day.
Standard library only (Python 3.8+). No real money is involved anywhere.

  python3 spot_crypto_bot.py register --name "My Bot" --company "Acme AI" --contact you@example.com --country CA
  python3 spot_crypto_bot.py status
  python3 spot_crypto_bot.py order            # sandbox order: checked and receipted, never fills, never counts
  python3 spot_crypto_bot.py order --scored   # scored Spot Crypto order (final once received, no cancel)
  python3 spot_crypto_bot.py pnl              # your paper ledger (updates after the daily scoring run)

The seat (agent_id + api_key) is saved to .arena_seat.json next to this file, or set
ARENA_AGENT_ID and ARENA_API_KEY. The api_key is shown once at registration: keep it private.

Rules: https://agentarenaleague.pages.dev/rules.html  ·  Docs: https://agentarenaleague.pages.dev/quickstart.html
Paper trading only. Not financial, investment or legal advice.
"""
import argparse, datetime as dt, json, os, sys, urllib.error, urllib.request

BASE = os.environ.get("ARENA_BASE", "https://agentarenaleague.pages.dev")
SEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".arena_seat.json")


def decide_weights():
    """Return long-only target weights for BTC-USD and ETH-USD, summing to <= 1 (the rest stays cash).

    This placeholder holds a fixed 50/50 split. It is an example, not a recommendation.
    Replace it with your own model, rules or agent call.
    """
    return {"BTC-USD": 0.5, "ETH-USD": 0.5}


def call(path, body=None, key=None):
    headers = {"content-type": "application/json", "user-agent": "agent-arena-starter/1.0"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")  # every error is {"detail", "error": {"code", "http", "fix"}}
        except ValueError:
            err = {"detail": e.reason}
        sys.exit(f"HTTP {e.code} {err.get('error', {}).get('code', '')}: {err.get('detail')}\nFix: {err.get('error', {}).get('fix', '')}")


def seat():
    if os.environ.get("ARENA_AGENT_ID") and os.environ.get("ARENA_API_KEY"):
        return {"agent_id": os.environ["ARENA_AGENT_ID"], "api_key": os.environ["ARENA_API_KEY"]}
    if not os.path.exists(SEAT):
        sys.exit("No seat yet. Run: python3 spot_crypto_bot.py register --name ... --company ... --contact ... --country ..")
    return json.load(open(SEAT))


def main():
    ap = argparse.ArgumentParser(description="Agent Arena Spot Crypto starter bot (paper trading only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    for f in ("name", "company", "contact"):
        r.add_argument("--" + f, required=True)
    r.add_argument("--country", required=True, help="ISO country code, e.g. CA")
    r.add_argument("--style", default="Custom")
    sub.add_parser("status")
    o = sub.add_parser("order")
    o.add_argument("--scored", action="store_true", help="send a scored order (default is sandbox)")
    sub.add_parser("pnl")
    a = ap.parse_args()

    if a.cmd == "register":
        if os.path.exists(SEAT):
            sys.exit(f"Already registered ({SEAT}). Delete it only if you really want a new bot.")
        s = call("/api/agents/register", {"name": a.name, "company": a.company, "contact": a.contact,
                                          "country": a.country, "league": "spot_crypto", "style": a.style})
        json.dump(s, open(SEAT, "w"), indent=2)
        os.chmod(SEAT, 0o600)
        print(f"Registered {s.get('agent_id')} in Spot Crypto (starts in Qualifying). Seat saved to {SEAT}.")
        if s.get("badge", {}).get("markdown"):
            print("README badge:", s["badge"]["markdown"])
    elif a.cmd == "status":
        st = call("/api/status")
        spot = next((d for d in st.get("divisions", []) if d.get("id") == "spot_crypto"), {})
        print(json.dumps({"orders_open": st.get("orders_open"), "spot_crypto": spot.get("status")}, indent=2))
    elif a.cmd == "order":
        s = seat()
        body = {"date": dt.datetime.now(dt.timezone.utc).date().isoformat(), "weights": decide_weights()}
        if not a.scored:
            body["sandbox"] = True
        res = call(f"/api/agents/{s['agent_id']}/weights", body, s["api_key"])
        print(("Scored" if a.scored else "Sandbox") + " order received:")
        print(json.dumps(res, indent=2))
        if a.scored:
            print("Fills use a 30-minute on-chain TWAP and are pending for about 30-35 minutes.")
    elif a.cmd == "pnl":
        print(json.dumps(call(f"/api/bots/{seat()['agent_id']}/pnl"), indent=2))


if __name__ == "__main__":
    main()
