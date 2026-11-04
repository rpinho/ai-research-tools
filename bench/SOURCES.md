# Data provenance and frozen local tests

Benchmark version: `mock-1104-v1`. Amendment scope is **two** past competitions
plus the existing exact-arithmetic toy. Only CSV bytes are downloaded; no mirror
notebooks, scripts, models, submissions, or third-party solutions are executed.

| Case | Finished competition | Original labeled training CSV | Metric (lower is better) | Final leaderboard | Data terms |
| --- | --- | --- | --- | --- | --- |
| concrete | [Playground S3E9, Concrete Strength](https://www.kaggle.com/competitions/playground-series-s3e9), 2023 | 5,407 rows, 277,194 bytes | RMSE | [Final standings](https://www.kaggle.com/competitions/playground-series-s3e9/leaderboard) | [Kaggle competition-specific rules](https://www.kaggle.com/competitions/playground-series-s3e9/rules) |
| crab | [Playground S3E16, Crab Age](https://www.kaggle.com/competitions/playground-series-s3e16), 2023 | 74,051 rows, 5,207,706 bytes | MAE | [Final standings](https://www.kaggle.com/competitions/playground-series-s3e16/leaderboard) | [Kaggle competition-specific rules](https://www.kaggle.com/competitions/playground-series-s3e16/rules) |

These are public competition datasets governed by the linked competition terms.
No separate SPDX/open-data license was verified for the CSV mirrors. In particular,
a mirror repository's code license must not be treated as a license for its data.
This PR redistributes **no competition data** and makes no claim that the data are
MIT-licensed. Kaggle's rules pages are dynamically rendered and were not fully
readable during setup; the linked terms, rather than an inferred license, are the
license reference. The Crab Age rules are indexed as allowing competition use and
commercial use. No such additional permission is inferred for Concrete Strength.

Anonymous Kaggle file downloads returned HTTP 401 during setup. To make the local
setup credential-free, the downloader uses the following public, commit-pinned
CSV mirrors, with byte count, row count and SHA-256 enforced before parsing:

| Case | Immutable data-only mirror | SHA-256 |
| --- | --- | --- |
| concrete | [CSV at `363caf6`](https://raw.githubusercontent.com/khushdeeppsingh/personal/363caf6fe5848923c11e34d8e81fbd0988c9c555/playground-series-s3e9/train.csv) | `6ce513646729e57c4e1eeedaa0310a2ca5e3ffef905dc09f3839b391fc393bb7` |
| crab | [CSV at `6c92088`](https://raw.githubusercontent.com/srikresna/playground-series-s3e16/6c92088508075bacd712049f75d70a96301a5b8b/train.csv) | `f22feaa2e7d667cf3ec95c897bb8d7f8018a8dab19c9f21e740911820d1efd69` |

Checksums pin the inspected mirror bytes; they are not an organizer signature or
proof of an official Kaggle-file checksum. The original unlabeled test CSVs and
public/private leaderboard membership are **not** downloaded or reconstructed.
Leaderboard links are historical context, never selection inputs. Local scores
cannot be compared with those leaderboard scores or used to infer a Kaggle rank.

## Local split, frozen before results

`cases.py` is the source of truth. `setup_data.py` writes an exclusive manifest
containing the complete specification, partition membership digests and every
prepared-file SHA-256. Later setup/runs verify it; edits fail instead of re-freezing.
Changing a source, split, budget, or assumption requires a new version/data root.

1. Exclude `id` and the target. SHA-256 the canonical JSON of remaining feature
   strings to define exact-feature groups. Identical features stay together even
   if targets differ. Neither grouping nor sampling reads target values.
2. Order groups by `SHA256("mock-1104-v1/sample/" + group)`; retain the first 1,600.
   Retain **all rows** in each selected group, so total rows can exceed 1,600.
3. Compute `SHA256("mock-1104-v1/split/" + group) mod 100`: 0–49 train,
   50–69 development validation, 70–84 sealed selection, 85–99 final hidden test.
4. The producer gets train/development targets and target-free selection/hidden
   features. Only the separate selector process opens selection/hidden targets.
   Reserve the one-use scoring ledger before either target file is opened.
5. Nominate once using sealed selection, then score only the frozen winner on
   final hidden targets. Final hidden scores never enter either selection gate.

| Case | Train rows (groups) | Dev rows (groups) | Selection rows (groups) | Final hidden rows (groups) | Prepared manifest SHA-256 |
| --- | ---: | ---: | ---: | ---: | --- |
| concrete | 1,387 (788) | 510 (331) | 487 (256) | 477 (225) | `3ea2d600222184b8f86b42f17ab3950ac4891a7e92e05ef29f879295e46d3f06` |
| crab | 811 (811) | 304 (304) | 237 (237) | 248 (248) | `3d1f8fcc0d0b45cdcf3e4461e0fc2009cdf463c257d4cfa7e40701e48d759674` |

The split is a **local mock split of public synthetic training data**, not the
organizer's original public/private test split. Feature grouping blocks exact
duplicate leakage; it does not establish independence of latent source records,
subjects, or the synthetic generator. This is a regression check, not a scientific
generalization claim. The source data are public, so these targets are operationally
withheld from the planner, not secret information or a contamination-free LLM test.

Case 0 reuses `examples/toy_demo.py`, its lane files, `sweep.check`, and the existing
v1 exact-arithmetic receipt mechanism. It enumerates squares from 0 through 9,
needs no download, and has **no statistical hidden test**. Both setups use this
same verifier. The toy's hidden score is null with a reason; the two exact repeats
in its legacy receipt are not independent statistical measurements.
