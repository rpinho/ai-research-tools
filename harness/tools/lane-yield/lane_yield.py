#!/usr/bin/env python3
"""lane-yield: the harness integration test for "a lane spending money in circles".

Why (2026-11-04): a lane ran ~38 agent workers for two days, used a whole week of model allowance, logged
~7,000 ideas and 528 claims, and its referee passed 98% of them. Nothing was worth emailing. Every check
in the harness was green, because they all measured activity (workers alive, cadence, claims written,
verdicts written), never yield. This test measures yield.

For each configured lane, over a rolling window (default 48 h) it computes:
  job_hours   wall-clock hours of agent jobs, from launcher logs ("=== run/attempt ... <date>" .. "exit ... <date>")
  new_yes     claims that say NEW: yes, pass result-lint and have a HOLDS verdict (something new and refereed),
              plus lines in the lane's results ledgers (`results`: JSONL written by log_result.py) with
              new=true and a refereed verdict, for lanes whose results are PRs, notes or submissions, not claims
  verdicts    referee verdicts written in the window, and the share that pass
and FAILS the lane when
  BURNING        job_hours >= max_job_hours_without_new and new_yes == 0
  RUBBER_STAMP   verdicts >= min_verdicts and pass share >= max_pass_share (a referee that passes everything
                 is not refereeing)
Every research lane is configured, not only one: the point of a lane is something new.
A lane with `report_only: true` is pushed to general on FAIL but gets no hold file.
With --act, a failing lane gets its hold file written (its loops stop spending) and a push to general.
Exit 0 = all lanes PASS, 1 = some lane FAILS, 2 = config error.
"""
import argparse, glob, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
LINT = HERE.parents[3] / 'tools' / 'result-lint'
sys.path.insert(0, str(LINT))
sys.path.insert(0, str(LINT.parent / 'sbs'))
from result_lint import lint  # noqa: E402

DEFAULTS = dict(window_hours=48, max_job_hours_without_new=20, min_verdicts=20, max_pass_share=0.9)
STAMP = re.compile(r'^(?:=== (?:run|attempt) .*?|exit \S+ )([A-Z][a-z]{2} [A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d) \S+ (\d{4})\s*$')


def parse_stamp(line):
    m = STAMP.match(line)
    if not m:
        return None
    return datetime.strptime(f'{" ".join(m.group(1).split())} {m.group(2)}', '%a %b %d %H:%M:%S %Y').timestamp()


def job_hours(paths, start, end):
    total = 0.0
    for p in paths:
        open_at = None
        try:
            lines = open(p, errors='replace').read().splitlines()
        except OSError:
            continue
        for line in lines:
            t = parse_stamp(line)
            if t is None:
                continue
            if line.startswith('==='):
                open_at = t
            elif open_at is not None:
                total += max(0.0, min(t, end) - max(open_at, start))
                open_at = None
        if open_at is not None and open_at < end:
            total += max(0.0, end - max(open_at, start))  # still running
    return total / 3600


def verdict_head(d):
    try:
        return next((l.strip() for l in open(d / 'VERDICT.md', errors='replace') if l.strip()), '')
    except OSError:
        return None


def check_lane(name, cfg, now):
    c = {**DEFAULTS, **cfg}
    end = now
    start = now - c['window_hours'] * 3600
    logs = [p for g in c.get('logs', []) for p in glob.glob(os.path.expanduser(g))]
    hours = job_hours(logs, start, end)
    claims = [Path(p) for g in c.get('claims', []) for p in glob.glob(os.path.expanduser(g)) if Path(p).is_dir()]
    verdicts = passes = new_yes = 0
    for d in claims:
        v = d / 'VERDICT.md'
        if v.is_file() and start <= v.stat().st_mtime <= end:
            verdicts += 1
            head = verdict_head(d) or ''
            ok = head.upper().startswith('HOLDS')
            passes += ok
            if ok and not lint(d, new_only=True):
                new_yes += 1
    for g in c.get('results', []):
        for p in glob.glob(os.path.expanduser(g)):
            try:
                rows = [json.loads(l) for l in open(p, errors='replace') if l.strip()]
            except (OSError, json.JSONDecodeError):
                continue
            for r in rows:
                try:
                    t = datetime.fromisoformat(str(r['utc']).replace('Z', '+00:00')).timestamp()
                except (KeyError, ValueError):
                    continue
                if start <= t <= end and r.get('new') is True and str(r.get('referee', '')).upper().startswith('HOLDS'):
                    new_yes += 1
    share = passes / verdicts if verdicts else 0.0
    fails = []
    if hours >= c['max_job_hours_without_new'] and new_yes == 0:
        fails.append(f'BURNING: {hours:.0f} job-hours in {c["window_hours"]} h and nothing new (0 refereed NEW: yes claims)')
    if verdicts >= c['min_verdicts'] and share >= c['max_pass_share']:
        fails.append(f'RUBBER_STAMP: referee passed {passes}/{verdicts} ({share:.0%}); a referee that passes everything is not refereeing')
    return dict(lane=name, as_of=datetime.fromtimestamp(now).isoformat(timespec='minutes'), job_hours=round(hours, 1),
                new_yes=new_yes, verdicts=verdicts, pass_share=round(share, 3), status='FAIL' if fails else 'PASS', fails=fails)


def act(row, cfg):
    hold = cfg.get('hold')
    if hold and not cfg.get('report_only'):
        p = Path(os.path.expanduser(hold))
        if not p.exists():
            p.write_text(f'HOLD by lane-yield {row["as_of"]}: ' + '; '.join(row['fails']) + '\nOnly general removes this file.\n')
    tg = Path.home() / '.claude/skills/orchestrator/scripts/tell-general'
    if tg.exists():
        subprocess.run([str(tg), 'ricardo', f'lane-yield FAIL {row["lane"]}: ' + '; '.join(row['fails'])], capture_output=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', default=os.path.expanduser('~/.config/lane-yield.json'))
    p.add_argument('--as-of', help='ISO time to evaluate at (backtest), default now')
    p.add_argument('--lane', action='append')
    p.add_argument('--act', action='store_true', help='write the hold file and push general for failing lanes')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    try:
        conf = json.load(open(a.config))
    except (OSError, json.JSONDecodeError) as e:
        print(f'config error: {e}', file=sys.stderr)
        return 2
    now = datetime.fromisoformat(a.as_of).timestamp() if a.as_of else time.time()
    rows = [check_lane(n, c, now) for n, c in conf.items() if not a.lane or n in a.lane]
    for r in rows:
        if a.act and r['status'] == 'FAIL':
            act(r, conf[r['lane']])
        if a.json:
            print(json.dumps(r))
        else:
            print(f'{r["status"]} {r["lane"]} @{r["as_of"]}: {r["job_hours"]} job-h, {r["new_yes"]} new, '
                  f'{r["verdicts"]} verdicts ({r["pass_share"]:.0%} pass)' + ''.join(f'\n  - {f}' for f in r['fails']))
    return 1 if any(r['status'] == 'FAIL' for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
