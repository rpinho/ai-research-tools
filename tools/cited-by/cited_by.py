#!/usr/bin/env python3
"""cited-by: who has cited this paper since it came out? (forward-citation prior-art check)

A result on a 2018 paper is most likely to be scooped by a paper that cites it. Searching arXiv for keywords
misses those. This prints the paper's year, its citing-paper count and the citing titles (newest first) from
OpenAlex, plus a ready `Cited by:` line for CLAIM.md (result-lint requires it on NEW: yes claims).

    cited_by.py 10.1016/j.cor.2021.105357
    cited_by.py arXiv:2011.13478
    cited_by.py "Monotone Lagrangians in cotangent bundles of spheres"

Read the closest titles yourself; the count alone proves nothing. Exit 0 found, 1 not found, 2 network error.
"""
import argparse, datetime as dt, json, re, sys, time, urllib.parse, urllib.request

API = 'https://api.openalex.org'


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ai-research-tools cited-by'})
            return json.load(urllib.request.urlopen(req, timeout=40))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def find(ref):
    m = re.search(r'(10\.\d{4,9}/\S+)', ref)
    if m:
        return get(f'{API}/works/doi:{m.group(1)}')
    m = re.search(r'(\d{4}\.\d{4,5})', ref)
    if m and re.search(r'arxiv', ref, re.I):
        try:
            return get(f'{API}/works/doi:10.48550/arXiv.{m.group(1)}')
        except Exception:
            pass
    r = get(f'{API}/works?search={urllib.parse.quote(ref)}&per-page=1')['results']
    return r[0] if r else None


def citing(work, limit=200):
    wid = work['id'].rsplit('/', 1)[-1]
    return get(f'{API}/works?filter=cites:{wid}&per-page={limit}&sort=publication_date:desc'
               '&select=display_name,publication_year,doi')['results']


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('ref', help='DOI, arXiv id or title')
    p.add_argument('--limit', type=int, default=200)
    a = p.parse_args(argv)
    try:
        w = find(a.ref)
    except Exception as e:
        print(f'network error: {e}', file=sys.stderr)
        return 2
    if not w:
        print('not found in OpenAlex; do the forward-citation check by hand (Google Scholar "Cited by")')
        return 1
    c = citing(w, a.limit)
    print(f"{w['display_name']} ({w['publication_year']}), cited by {w['cited_by_count']}")
    for x in c:
        print(f"  {x['publication_year']}  {x['display_name'][:120]}  {x.get('doi') or ''}")
    today = dt.date.today().isoformat()
    print(f"\nCited by: {w['cited_by_count']} papers since {w['publication_year']} "
          f"(OpenAlex {w['id'].rsplit('/', 1)[-1]}, checked {today}); closest: <title, or none>")
    return 0


if __name__ == '__main__':
    sys.exit(main())
