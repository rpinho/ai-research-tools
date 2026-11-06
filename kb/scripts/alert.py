#!/usr/bin/env python3
"""
alert.py: one alerts directory that every scheduled job writes to, and every reader drains.

WHY. A pipeline went blind for three nights (an upstream API ran out of credit and returned an error
body with exit 0). It surfaced as a question at the bottom of the third digest. The human's verdict:
"you have to tell me this right away". And: email does not work as an alarm, because email is
exactly what gets ignored. So:

  1. FIRST, every pipeline guarantees an output (a fallback digest, a degraded page). The fix is the
     output; the alert is the backstop.
  2. Any job that detects a failure calls `alert.py raise`. One file per problem key, idempotent, so
     a job that fails every 10 minutes does not produce 144 alerts.
  3. Readers drain the same directory:
       - the digest prints `alert.py list --md` as the FIRST lines of its top section;
       - a push loop (an interactive agent session's recurring task, cron, or launchd) runs
         `alert.py push-pending` and sends each line through whatever reaches the human's phone
         (the agent harness's push-notification tool, ntfy, a chat webhook), then marks it pushed;
       - the job that raised it calls `alert.py clear` once the problem is gone.

The directory lives OUTSIDE the repo (default ~/.local/share/agent-kb/alerts, or $KB_ALERTS_DIR) so
jobs never dirty the git tree they are about to land into.

USAGE
  alert.py raise --key api-credit --msg "Source API returned HTTP 402; tonight's pull is empty"
  alert.py clear --key api-credit
  alert.py list [--md]            # all open alerts
  alert.py push-pending           # print alerts not yet pushed (one per line) and mark them pushed
"""
import argparse, datetime, json, os, re, sys
from pathlib import Path

DIR = Path(os.environ.get("KB_ALERTS_DIR", Path.home() / ".local" / "share" / "agent-kb" / "alerts"))


def path_for(key):
    return DIR / (re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-") + ".json")


def load_all():
    out = []
    for p in sorted(DIR.glob("*.json")) if DIR.exists() else []:
        try:
            out.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("raise"); r.add_argument("--key", required=True); r.add_argument("--msg", required=True)
    c = sub.add_parser("clear"); c.add_argument("--key", required=True)
    l = sub.add_parser("list"); l.add_argument("--md", action="store_true")
    sub.add_parser("push-pending")
    a = ap.parse_args()
    now = datetime.datetime.now().replace(microsecond=0).isoformat()

    if a.cmd == "raise":
        DIR.mkdir(parents=True, exist_ok=True)
        p = path_for(a.key)
        old = json.loads(p.read_text()) if p.exists() else {}
        same = old.get("msg") == a.msg and old.get("first", "")[:10] == now[:10]
        rec = {"key": a.key, "msg": a.msg, "first": old.get("first", now) if same else now, "last": now,
               "count": old.get("count", 0) + 1 if same else 1, "pushed": old.get("pushed", False) if same else False}
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        print(f"alert raised: {a.key} ({'repeat' if same else 'new'})")
    elif a.cmd == "clear":
        p = path_for(a.key)
        if p.exists():
            p.unlink()   # generated state outside the repo, not user data
            print(f"alert cleared: {a.key}")
    elif a.cmd == "list":
        items = load_all()
        if a.md:
            print("\n".join(f"- ALERT ({d['first'][:16].replace('T', ' ')}): {d['msg']}" for _, d in items) or "(no open alerts)")
        else:
            for _, d in items:
                print(f"{d['key']}\t{d['first']}\t{d['msg']}")
    elif a.cmd == "push-pending":
        for p, d in load_all():
            if not d.get("pushed"):
                print(d["msg"][:200])
                d["pushed"] = True
                p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
