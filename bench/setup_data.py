#!/usr/bin/env python3
"""Download CSV data only, verify pinned SHA256, and freeze local mock splits."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bench.cases import CASES, VERSION, canonical, digest, freeze_spec


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def write_csv(path, fields, rows):
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def prepare(case, root):
    """Existing preparations are verified, never silently replaced or re-frozen."""
    source = CASES[case]
    destination = root / case
    manifest_path = destination / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['spec'] != freeze_spec(case):
            raise ValueError('frozen specification changed; create a new version and data root')
        for name, checksum in manifest['files'].items():
            if file_sha(destination / name) != checksum:
                raise ValueError('frozen data checksum mismatch: ' + name)
        return manifest
    if destination.exists():
        raise ValueError('incomplete preparation exists; preserve it and use a fresh data root')
    with urllib.request.urlopen(source['url'], timeout=30) as response:
        payload = response.read(20 * 1024 * 1024 + 1)
    if len(payload) != source['bytes'] or hashlib.sha256(payload).hexdigest() != source['sha256']:
        raise ValueError('source size or SHA256 mismatch: ' + case)
    reader = csv.DictReader(io.StringIO(payload.decode('utf-8')))
    fields = reader.fieldnames
    rows = list(reader)
    if len(rows) != source['rows'] or source['target'] not in fields or 'id' not in fields:
        raise ValueError('source schema or row count mismatch')
    features = [f for f in fields if f not in ('id', source['target'])]
    groups = {}
    for row in rows:
        key = digest({field: row[field] for field in features})
        groups.setdefault(key, []).append(row)
    # Sampling and partitioning use no target values. Keep exact feature duplicates together.
    selected = sorted(groups, key=lambda g: hashlib.sha256((VERSION+'/sample/'+g).encode()).hexdigest())[:source['max_groups']]
    partitions = {name: [] for name in ('train', 'dev', 'selection', 'hidden')}
    group_ids = {}
    for group in selected:
        bucket = int(hashlib.sha256((VERSION+'/split/'+group).encode()).hexdigest(), 16) % 100
        name = 'train' if bucket < 50 else 'dev' if bucket < 70 else 'selection' if bucket < 85 else 'hidden'
        for row in groups[group]:
            partitions[name].append(dict(row, group=group))
            group_ids[row['id']] = group
    if any(len(value) < 20 for value in partitions.values()):
        raise ValueError('insufficient data in frozen partition')
    destination.mkdir(parents=True)
    for partition, records in partitions.items():
        # Producers receive target-free selection and hidden features only.
        public_fields = ['id', 'group'] + features
        if partition in ('train', 'dev'):
            public_fields.append(source['target'])
        write_csv(destination / (partition+'.csv'), public_fields,
                  [{f: row[f] for f in public_fields} for row in records])
        if partition in ('selection', 'hidden'):
            write_csv(destination / (partition+'-targets.csv'), ['id', 'group', source['target']],
                      [{f: row[f] for f in ('id', 'group', source['target'])} for row in records])
    files = {path.name: file_sha(path) for path in sorted(destination.glob('*.csv'))}
    manifest = dict(spec=freeze_spec(case), files=files,
                    partitions={name: dict(rows=len(value), groups=len({r['group'] for r in value}),
                                          ids_sha256=digest([r['id'] for r in value]))
                                for name, value in partitions.items()})
    write_json(manifest_path, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=REPO/'bench/data')
    args = parser.parse_args(argv)
    args.data_root.mkdir(parents=True, exist_ok=True)
    with (args.data_root/'setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for case in CASES:
            manifest = prepare(case, args.data_root)
            print(case, canonical(manifest['partitions']), 'manifest_sha256='+file_sha(args.data_root/case/'manifest.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
