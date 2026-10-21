"""Decode only a complete, digest-bound packet with positive cleanup evidence.

Usage: python3 -I -S decode.py RAW_LOG NEW_DIRECTORY RUN_ID COMMIT MANIFEST_SHA
This source has not been executed. No registry upload is implemented.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys

NAMES = {'task_handoff_check-0.1.1.tar.gz', 'task_handoff_check-0.1.1-py3-none-any.whl',
         'delivery_evidence_check-0.2.0.tar.gz', 'delivery_evidence_check-0.2.0-py3-none-any.whl',
         'packaging-receipt.json'}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'duplicate JSON key')
        out[key] = value
    return out


def main():
    require(len(sys.argv) == 6, 'arguments')
    log, target = map(Path, sys.argv[1:3])
    run, commit, manifest = sys.argv[3:]
    require(re.fullmatch(r'[0-9]{1,20}', run) and re.fullmatch(r'[a-f0-9]{40}', commit) and
            re.fullmatch(r'[a-f0-9]{64}', manifest), 'identity')
    require(log.is_file() and not log.is_symlink() and log.stat().st_size <= 32 * 1024**2, 'raw log limit')
    require(not target.exists() and not target.is_symlink(), 'new output directory required')
    identity = {'run': run, 'commit': commit, 'manifest': manifest}
    begun = ended = closed = False
    packet = hashlib.sha256()
    output, metadata = {}, {}
    current = None
    encoded = []
    next_chunk = 0
    task_bytes = 0

    def finish():
        nonlocal current, encoded, next_chunk
        if current is None:
            return
        info = metadata[current]
        require(next_chunk == info['chunks'], 'missing chunks')
        raw = base64.b64decode(''.join(encoded), validate=True)
        require(len(raw) == info['bytes'] and hashlib.sha256(raw).hexdigest() == info['sha256'], 'file digest')
        output[current] = raw
        current, encoded, next_chunk = None, [], 0

    with log.open('rb') as source:
        for rawline in source:
            require(len(rawline) <= 16384, 'line limit')
            at = rawline.find(b'PKGTWO ')
            if at < 0:
                continue
            # Standard provider timestamp prefixes are allowed, never arbitrary log fragments.
            prefix = rawline[:at]
            require(not prefix or re.fullmatch(rb'\d{4}-\d\d-\d\dT[0-9:.]+Z ', prefix), 'marker prefix')
            line = rawline[at:]
            require(line.endswith(b'\n') and b'\r' not in line, 'packet newline')
            task_bytes += len(line)
            require(task_bytes <= 8 * 1024**2 and not closed, 'stream limit or extra packet')
            value = json.loads(line[7:], object_pairs_hook=unique)
            require(isinstance(value, dict), 'record type')
            kind = value.get('kind')
            if kind == 'begin':
                require(not begun and set(value) == {'kind', *identity} and
                        all(value[k] == v for k, v in identity.items()), 'begin binding')
                begun = True
            elif kind == 'file':
                require(begun and not ended, 'file ordering')
                finish()
                require(set(value) == {'kind', 'name', 'bytes', 'sha256', 'chunks'}, 'file fields')
                name = value['name']
                require(name in NAMES and name not in metadata, 'file allowlist')
                limit = 65536 if name.endswith('.json') else 4 * 1024**2
                require(type(value['bytes']) is int and 0 < value['bytes'] <= limit and
                        type(value['chunks']) is int and value['chunks'] == (4 * ((value['bytes'] + 2) // 3) + 4095) // 4096 and
                        re.fullmatch(r'[a-f0-9]{64}', value['sha256']), 'file limits')
                metadata[name] = value
                require(sum(x['bytes'] for k, x in metadata.items() if not k.endswith('.json')) <= 4 * 1024**2,
                        'aggregate archive limit')
                current = name
            elif kind == 'chunk':
                require(begun and not ended and current is not None and
                        set(value) == {'kind', 'name', 'index', 'data'}, 'chunk ordering')
                require(value['name'] == current and type(value['index']) is int and value['index'] == next_chunk and
                        next_chunk < metadata[current]['chunks'] and isinstance(value['data'], str) and
                        0 < len(value['data']) <= 4096 and re.fullmatch(r'[A-Za-z0-9+/]*={0,2}', value['data']), 'chunk binding')
                encoded.append(value['data'])
                next_chunk += 1
            elif kind == 'end':
                require(begun and not ended and set(value) == {'kind', 'files'} and value['files'] == 5, 'end')
                finish()
                require(set(output) == NAMES, 'complete output set')
                ended = True
            elif kind == 'closed':
                require(ended and set(value) == {'kind', *identity, 'cleanup', 'success', 'packet_sha256'} and
                        all(value[k] == v for k, v in identity.items()) and value['cleanup'] is True and
                        value['success'] is True and value['packet_sha256'] == packet.hexdigest(), 'positive cleanup binding')
                closed = True
            else:
                raise ValueError('unknown record')
            if kind != 'closed':
                packet.update(line)
    require(begun and ended and closed, 'incomplete cleanup-qualified packet')
    receipt = json.loads(output['packaging-receipt.json'], object_pairs_hook=unique)
    require(receipt.get('status') == 'PACKAGING_AND_CONSUMER_CHECKS_PASS' and receipt.get('published') is False,
            'receipt status')
    # Only after complete validation create a fresh directory. No existing file is replaced.
    target.mkdir(mode=0o700)
    for name, data in output.items():
        fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    with (target / 'DECODE-RECEIPT.json').open('x') as stream:
        json.dump({'schema': 1, **identity, 'raw_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
                   'packet_sha256': packet.hexdigest(), 'cleanup': True, 'published': False,
                   'files': {k: {'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k, v in output.items()}},
                  stream, sort_keys=True, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
