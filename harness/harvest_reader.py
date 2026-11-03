"""Read-only pod reader. Executed in memory by python3 -B, never installed on pods."""
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import PurePosixPath
import re
import stat
import subprocess
import sys

MAX_BYTES = 200_000_000
MEDIA = frozenset('.mp3 .mp4 .m4a .m4v .wav .wave .avi .mov .mkv .webm .flac .ogg .oga .ogv .opus .aac .aiff .aif .wma .wmv .mpeg .mpg .3gp .ts .mts .m2ts .mid .midi .amr .au'.split())


def valid(path):
    return (isinstance(path, str) and path.startswith('/workspace/')
            and str(PurePosixPath(path)) == path and '..' not in path.split('/')
            and not any(ord(c) < 32 for c in path))


def opened(path, directory=False):
    """No symlinks in any component; O_NONBLOCK prevents FIFO hangs."""
    if not valid(path):
        raise ValueError('path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        pieces = path.strip('/').split('/')
        for i, piece in enumerate(pieces):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(pieces) - 1 or directory:
                flags |= os.O_DIRECTORY
            nxt = os.open(piece, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def media(path, head):
    mime = mimetypes.guess_type(path)[0] or ''
    return (PurePosixPath(path).suffix.lower() in MEDIA or mime.startswith(('audio/', 'video/'))
            or head.startswith((b'fLaC', b'OggS', b'ID3', b'\x1aE\xdf\xa3'))
            or head[:4] == b'RIFF' and head[8:12] in (b'WAVE', b'AVI ')
            or head[4:8] == b'ftyp')


def inspect(path):
    fd = opened(path)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):
            return dict(path=path, skip='special')
        if before.st_size > MAX_BYTES:
            return dict(path=path, skip='over_200_MB', size=before.st_size)
        head = f.read(16)
        if media(path, head):
            return dict(path=path, skip='media', size=before.st_size)
        digest = hashlib.sha256(head)
        size = len(head)
        while True:
            block = f.read(262144)
            if not block:
                break
            size += len(block)
            if size > MAX_BYTES:
                raise ValueError('growing')
            digest.update(block)
        after = os.fstat(f.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != after.st_size:
            raise ValueError('changed')
        return dict(path=path, size=size, sha256=digest.hexdigest())


def manifest(roots):
    for root in roots:
        try:
            fd = opened(root, directory=True)
            os.close(fd)
            # fwalk with follow_symlinks=False; inspect also guards every parent.
            def failed(error):
                raise error
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
                for name in sorted(dirs + files):
                    path = directory + '/' + name
                    info = os.lstat(path)
                    if stat.S_ISLNK(info.st_mode):
                        if name in dirs:
                            dirs.remove(name)
                        yield dict(path=path, skip='symlink')
                    elif name in files:
                        try:
                            yield inspect(path)
                        except (OSError, ValueError):
                            yield dict(path=path, error='unreadable_or_changed')
        except (OSError, ValueError):
            yield dict(path=root, error='results_path_unavailable')


def deadline_seconds():
    """Only sleep processes, never start-command credentials or environment."""
    try:
        out = subprocess.run(['ps', '-C', 'sleep', '-o', 'etimes=,ppid=,args='],
                             capture_output=True, text=True, timeout=5).stdout
        remaining = []
        for line in out.splitlines():
            m = re.fullmatch(r'\s*(\d+)\s+(\d+)\s+(?:/\S*/)?sleep\s+(\d+)\s*', line)
            if m and int(m[2]) != 1 and int(m[3]) >= 3600:
                left = int(m[3]) - int(m[1])
                if left > 0:
                    remaining.append(left)
        return min(remaining) if remaining else None
    except (OSError, subprocess.SubprocessError):
        return None


def serve(token, roots):
    def emit(row):
        print('\n' + token + base64.b64encode(json.dumps(row).encode()).decode(), flush=True)
    emit(dict(ready=True))
    pending = []
    for line in sys.stdin:
        try:
            encoded = line.strip()
            if encoded.startswith('+'):
                pending.append(encoded[1:])
                if sum(map(len, pending)) > 65536:
                    raise ValueError('request limit')
                emit(dict(request_chunk=True))
                continue
            if encoded == '.':
                encoded = ''.join(pending)
                pending = []
            request = json.loads(base64.b64decode(encoded))
            if request['op'] in ('manifest', 'next'):
                first = request['op'] == 'manifest'
                if first:
                    inventory = iter(manifest(roots))
                exhausted = False
                for _ in range(16):
                    try:
                        emit(next(inventory))
                    except StopIteration:
                        exhausted = True
                        break
                response = dict(end=True, more=not exhausted)
                if first:
                    response['deadline_seconds'] = deadline_seconds()
                emit(response)
            elif request['op'] == 'get':
                path = request['path']
                if not any(path.startswith(root + '/') for root in roots):
                    raise ValueError('outside roots')
                expected = inspect(path)
                if expected != dict(path=path, size=request['size'], sha256=request['sha256']):
                    raise ValueError('changed')
                with os.fdopen(opened(path), 'rb') as f:
                    digest, size = hashlib.sha256(), 0
                    while True:
                        block = f.read(49152)
                        if not block:
                            break
                        size += len(block)
                        if size > MAX_BYTES:
                            raise ValueError('size')
                        digest.update(block)
                        emit(dict(data=base64.b64encode(block).decode()))
                    if size != expected['size'] or digest.hexdigest() != expected['sha256']:
                        raise ValueError('changed')
                emit(dict(end=True, size=size, sha256=digest.hexdigest()))
            elif request['op'] == 'quit':
                return
            else:
                raise ValueError('operation')
        except (OSError, ValueError, KeyError, TypeError):
            emit(dict(end=True, error='remote_read_failed'))
