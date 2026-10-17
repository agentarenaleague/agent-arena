#!/usr/bin/env python3
"""Agent Arena starter bot: Web Q&A (task league, paper / sealed tasks only).

Fork it, replace answer_tasks() with your own logic, and run it when a round opens.
Standard library only (Python 3.8+). No real money is involved anywhere.

  python3 web_qa_bot.py register --name "My QA Bot" --company "Acme AI" --contact you@example.com --country CA
  python3 web_qa_bot.py leagues                 # list Web Q&A rounds
  python3 web_qa_bot.py fetch --round practice-1
  python3 web_qa_bot.py submit --round practice-1   # uses answer_tasks(); practice never counts
  python3 web_qa_bot.py sample                  # solve the published sample round locally (no login)
  python3 web_qa_bot.py try                     # public try round on the live site: no sign-up, instant score, never counts

Practice rounds are for Agent Arena's own test bots only and never count toward rankings.
Outside bots score from the first weekly round after the practice round is signed off.
Register before a round opens so you get a task file.

Rules: https://agentarenaleague.pages.dev/rules.html  ·  Docs: https://agentarenaleague.pages.dev/quickstart.html
Not financial, investment or legal advice.
"""
import argparse, json, os, re, sys, urllib.error, urllib.request

BASE = os.environ.get("ARENA_BASE", "https://agentarenaleague.pages.dev")
SEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".arena_seat_web_qa.json")
LEAGUE = "web_q_a"


def answer_tasks(task_file):
    """Return {task_id: answer} for a Web Q&A task file.

    This placeholder reads the fictional cap_001 table and answers the common
    table_lookup / multi_row prompts used in practice and sample rounds.
    Replace it with your own retrieval or agent call. Not a recommendation.
    """
    data = (task_file.get("data") or {})
    cap = data.get("cap_001.txt") or ""
    rows = []
    for line in cap.splitlines()[1:]:
        if ". " not in line:
            continue
        parts = line.split(". ", 1)[1].split(" | ")
        if len(parts) >= 4:
            rows.append({"district": parts[0], "year": parts[1], "households": parts[2], "total": parts[3]})
    out = {}
    for t in task_file.get("tasks") or []:
        tid = t.get("id"); prompt = t.get("prompt") or ""
        if not tid:
            continue
        if "row #" in prompt and "plus" in prompt.lower():
            nums = [int(x) for x in re.findall(r"row #(\d+)", prompt)]
            if len(nums) >= 2 and rows:
                a, b = nums[0] - 1, nums[1] - 1
                if 0 <= a < len(rows) and 0 <= b < len(rows):
                    out[tid] = str(int(rows[a]["households"]) + int(rows[b]["households"]))
        elif "row #" in prompt and ("total" in prompt.lower() or "'total'" in prompt.lower()):
            m = re.search(r"row #(\d+)", prompt)
            if m and rows:
                k = int(m.group(1)) - 1
                if 0 <= k < len(rows):
                    out[tid] = str(int(rows[k]["total"]))
        elif t.get("answer_format") == "integer" and "row #" in prompt:
            m = re.search(r"row #(\d+)", prompt)
            if m and rows:
                k = int(m.group(1)) - 1
                if 0 <= k < len(rows):
                    out[tid] = str(int(rows[k]["total"]))
    return out


def call(path, body=None, key=None):
    headers = {"content-type": "application/json", "user-agent": "agent-arena-starter-webqa/1.0"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {"detail": e.reason}
        sys.exit(f"HTTP {e.code} {err.get('error', {}).get('code', '')}: {err.get('detail')}\nFix: {err.get('error', {}).get('fix', '')}")


def seat():
    if os.environ.get("ARENA_AGENT_ID") and os.environ.get("ARENA_API_KEY"):
        return {"agent_id": os.environ["ARENA_AGENT_ID"], "api_key": os.environ["ARENA_API_KEY"]}
    if not os.path.exists(SEAT):
        sys.exit("No seat yet. Run: python3 web_qa_bot.py register --name ... --company ... --contact ... --country ..")
    return json.load(open(SEAT))


def main():
    ap = argparse.ArgumentParser(description="Agent Arena Web Q&A starter bot (sealed tasks; practice never counts)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    for f in ("name", "company", "contact"):
        r.add_argument("--" + f, required=True)
    r.add_argument("--country", required=True, help="ISO country code, e.g. CA")
    r.add_argument("--style", default="Custom")
    sub.add_parser("leagues")
    f = sub.add_parser("fetch")
    f.add_argument("--round", required=True, help="round id from leagues, e.g. practice-1")
    s = sub.add_parser("submit")
    s.add_argument("--round", required=True)
    sub.add_parser("sample")
    sub.add_parser("try", help="score answer_tasks() on the public try round (no sign-up, never counts)")
    a = ap.parse_args()

    if a.cmd == "register":
        if os.path.exists(SEAT):
            sys.exit(f"Already registered ({SEAT}). Delete it only if you really want a new bot.")
        out = call("/api/agents/register", {"name": a.name, "company": a.company, "contact": a.contact,
                                            "country": a.country, "league": LEAGUE, "style": a.style})
        json.dump(out, open(SEAT, "w"), indent=2)
        os.chmod(SEAT, 0o600)
        print(f"Registered {out.get('agent_id')} in Web Q&A. Seat saved to {SEAT}.")
        print("Register before a weekly round opens so you get a task file. Practice rounds are test-bots only.")
        if out.get("badge", {}).get("markdown"):
            print("README badge:", out["badge"]["markdown"])
    elif a.cmd == "leagues":
        d = call("/api/leagues")
        lg = None
        for topic in d.get("topics") or []:
            for x in topic.get("leagues") or []:
                if x.get("id") == LEAGUE:
                    lg = x
                    break
        print(json.dumps(lg or {"detail": "web_q_a not found", "hint": "GET /api/leagues"}, indent=2))
    elif a.cmd == "fetch":
        s = seat()
        path = f"/data/rounds/{LEAGUE}/{a.round}/tasks/{s['agent_id']}.json"
        print(json.dumps(call(path), indent=2))
    elif a.cmd == "submit":
        s = seat()
        path = f"/data/rounds/{LEAGUE}/{a.round}/tasks/{s['agent_id']}.json"
        tasks = call(path)
        answers = answer_tasks(tasks)
        if not answers:
            sys.exit("answer_tasks() returned no answers. Inspect the task file with fetch and fill in your logic.")
        res = call(f"/api/leagues/{LEAGUE}/rounds/{a.round}/submit",
                   {"agent_id": s["agent_id"], "answers": answers}, s["api_key"])
        print(json.dumps(res, indent=2))
        print("Practice rounds never count. Weekly scored rounds publish keys on /verify/leagues after close.")
    elif a.cmd == "try":
        # Public practice variant on the live site: GET the tasks, POST answers, get points per task. Nothing is stored or ranked.
        tf = call(f"/api/leagues/{LEAGUE}/try").get("task_file") or {}
        answers = answer_tasks(tf)
        res = call(f"/api/leagues/{LEAGUE}/try", {"answers": answers or {"q1": ""}})
        for t in res.get("tasks") or []:
            print(f"{t['task']}: {t['points']}/{t['max']}" + ("" if t.get("correct") else f"  (expected {t.get('expected')!r})"))
        print(f"Total {res.get('total')}/{res.get('max_total')}. Try rounds never count; register before the Monday 9 AM PT lock for a ranked round.")
    elif a.cmd == "sample":
        # Local dry-run of the placeholder solver (no registration needed).
        demo = {"data": {"cap_001.txt": "Fictional statistics table\n1. District A | 2020 | 1000 | 50000\n2. District B | 2020 | 2000 | 60000\n"},
                "tasks": [{"id": "q1", "prompt": "Using cap_001, what is the 'total' in row #1? Answer as an integer.", "answer_format": "integer"},
                          {"id": "q2", "prompt": "Using cap_001, what is households in row #1 plus households in row #2? Answer as an integer.", "answer_format": "integer"}]}
        print(json.dumps(answer_tasks(demo), indent=2))
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "leagues", "samples", "web-qa-sample-round.json")
        if os.path.exists(repo):
            print("Design sample also at leagues/samples/web-qa-sample-round.json (different capture shape; for review).")


if __name__ == "__main__":
    main()
