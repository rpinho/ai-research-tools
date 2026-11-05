"""Short maintenance ticks dispatch independent, per-identity locked workers."""
import fcntl
import harvest_dedupe
from pathlib import Path
import subprocess
import sys


def schedule(root, entries):
    # launchd must set AbandonProcessGroup: workers outlive this fast tick.
    directory = root / '.pod-maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    failed = False
    for pod in entries:
        with (directory / (pod['pod_id'] + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue  # This pod is still copying; schedule all the others.
        # Release the preflight lock before spawning: a fast child must be able
        # to acquire it immediately. Concurrent schedulers can spawn duplicates;
        # run() still admits exactly one worker for this identity.
        try:
            with (directory / (pod['pod_id'] + '.log')).open('a') as log:
                subprocess.Popen([sys.executable, str(Path(__file__).with_name('rh.py')),
                    '--root', str(root), 'pods', 'maintain', pod['name'], '--pod-id', pod['pod_id']],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, close_fds=True)
        except OSError:
            failed = True
    return int(failed)


def run(root, name, pod_id, rh, pods):
    directory = root / '.pod-maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (pod_id + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        pod = next((p for p in pods.registry(root) if p['name'] == name and p['pod_id'] == pod_id), None)
        if pod is None:
            return 0  # Removed/replaced since scheduling: never act on another ID.
        harvested = 1
        try:
            harvested = pods.harvest.run(root, [pod], rh, name=name)
        except Exception:
            print('rh pods: periodic harvest failed', file=sys.stderr)
        # A registration may be removed/retargeted while a long copy runs.
        # Only the unchanged registered declaration may start local dedupe.
        if harvested == 0 and pod in pods.registry(root):
            try:
                result = harvest_dedupe.maintenance(root, pod)
                if result is not None:
                    import json
                    print(json.dumps(result, sort_keys=True), flush=True)
            except Exception:
                print('rh pods: periodic dedupe failed', file=sys.stderr)
                harvested = 1
        # Preserve the existing registry/reaper serialization and shutdown
        # gates. Sampling and all other pods' copies use independent locks.
        # Cancellation propagates without beginning any reaper operation.
        with (root / '.pods.lock').open('a') as registry_lock:
            try:
                fcntl.flock(registry_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 1
            current = next((p for p in pods.registry(root) if p['pod_id'] == pod_id), None)
            reaped = pods.reap(root, [current], rh, quiet=True) if current == pod else 0
        return harvested or reaped
