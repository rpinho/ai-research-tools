#!/usr/bin/env python3
"""Simple baseline strength (Ellington, Wang, Addagudi and Cole 2026, arXiv 2610.40700).

    SBS = (M_simple - M_trivial) / (M_method - M_trivial)

The fraction of a method's gain over a trivial predictor that a simple baseline already recovers.
Works for higher-is-better and lower-is-better metrics alike (numerator and denominator flip together).
SBS >= 1: the method adds nothing over the simple baseline. SBS <= 0: the baseline is no better than trivial.
The method must beat the trivial predictor (M_method != M_trivial, in the method's favour), or SBS is undefined.
"""
import argparse, json, math, sys


def sbs(trivial, simple, method, higher_is_better=True):
    vals = [float(trivial), float(simple), float(method)]
    if not all(math.isfinite(v) for v in vals):
        raise ValueError('scores must be finite numbers')
    t, s, m = vals
    gain = (m - t) if higher_is_better else (t - m)
    if gain <= 0:
        raise ValueError('the method does not beat the trivial predictor, so SBS is undefined: report that instead')
    return (s - t) / (m - t)


def verdict(value):
    if value >= 1:
        return 'simple baseline matches or beats the method: the method adds nothing over it'
    if value <= 0:
        return 'simple baseline is no better than the trivial predictor'
    return f'simple baseline recovers {value:.1%} of the method\'s gain over trivial'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--trivial', type=float, required=True, help='trivial predictor score (constant / majority class)')
    p.add_argument('--simple', type=float, required=True, help='simple baseline score (linear / boosted trees)')
    p.add_argument('--method', type=float, required=True, help='score of the method being claimed')
    p.add_argument('--lower-is-better', action='store_true', help='for RMSE, MAE, loss, error rates')
    p.add_argument('--metric', default='', help='metric name, recorded in the output')
    a = p.parse_args(argv)
    hib = not a.lower_is_better
    try:
        v = sbs(a.trivial, a.simple, a.method, hib)
    except ValueError as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        return 2
    print(json.dumps({'metric': a.metric, 'higher_is_better': hib, 'trivial': a.trivial, 'simple': a.simple,
                      'method': a.method, 'sbs': round(v, 6), 'verdict': verdict(v)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
