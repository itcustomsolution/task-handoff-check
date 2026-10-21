"""Fixed five-download acquisition and offline packaging; source preparation only."""
import hashlib
import ctypes
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

CONTROL = Path('/control')
INPUTS = Path('/inputs')
WORK = Path('/work')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def load(path):
    require(path.stat().st_size <= 262144, 'json-limit')
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(65536), b''):
            h.update(data)
    return h.hexdigest()


def safe(name):
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and '\\' not in name and
            all(x not in ('', '.', '..') for x in name.split('/')), 'unsafe-member')
    return p


def command(item, deadline):
    """Bound pipes while running; never forward package-controlled stdout to CI."""
    require(item['max_invocations'] == 1 and time.monotonic() < deadline, 'command-admission')
    start = time.monotonic()
    end = min(start + item['wall_seconds'], deadline, float(os.environ['PACKAGING_PHASE_DEADLINE']))
    proc = subprocess.Popen(item['argv'], cwd=item['cwd'], env=dict(os.environ),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    parts = {'stdout': bytearray(), 'stderr': bytearray()}
    sel = selectors.DefaultSelector()
    for stream, name in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
        os.set_blocking(stream.fileno(), False)
        sel.register(stream, selectors.EVENT_READ, name)
    try:
        while sel.get_map() or proc.poll() is None:
            require(time.monotonic() < end, 'command-timeout-' + item['id'])
            for key, _ in sel.select(min(0.1, max(0.001, end - time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    sel.unregister(key.fileobj)
                    key.fileobj.close()
                else:
                    parts[key.data].extend(data)
                    require(len(parts[key.data]) <= 131072, 'command-output-limit')
        rc = proc.wait(timeout=1)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)
        sel.close()
    # This admitted PID namespace contains only this orchestrator and owned
    # descendants. Reap adopted zombies; any surviving command child is a failure.
    while True:
        try:
            child, _ = os.waitpid(-1, os.WNOHANG)
            if child == 0:
                break
        except ChildProcessError:
            break
    require(not [x.name for x in Path('/proc').iterdir() if x.name.isdigit() and int(x.name) != os.getpid()],
            'command-descendants-remain')
    out = bytes(parts['stdout'])
    err = bytes(parts['stderr'])
    negative = item['id'] in {x['id'] for x in load(INPUTS / 'CONSUMER-VALIDATION.json')['cases'] if x['exit'] == 1}
    require(rc == (1 if negative else 0), 'command-exit-' + item['id'])
    if item['id'].endswith('-version'):
        expected = b'0.1.1\n' if item['id'].startswith('task-') else b'0.2.0\n'
        require(out == expected, 'version-output')
    cases = {x['id']: x for x in load(INPUTS / 'CONSUMER-VALIDATION.json')['cases']}
    if item['id'] in cases:
        result = json.loads(out)
        case = cases[item['id']]
        require(result.get('ok') is case['ok'] and result.get('scope') == case['scope'], 'consumer-output')
    result = {'id': item['id'], 'argv': item['argv'], 'exit': rc,
            'elapsed_seconds': round(time.monotonic() - start, 4),
            'stdout_bytes': len(out), 'stderr_bytes': len(err),
            'stdout_sha256': hashlib.sha256(out).hexdigest(), 'stderr_sha256': hashlib.sha256(err).hexdigest()}
    if item['id'] in ('raw-python-preflight', 'toolchain-identity'):
        require(len(out) <= 2048, 'identity-output-limit')
        result['identity_output'] = out.decode('ascii')
    return result


class Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, original):
        self.original = original
        self.count = 0

    def http_error_302(self, req, fp, code, msg, headers):
        self.count += 1
        newurl = urllib.parse.urljoin(req.full_url, headers.get('Location', ''))
        u = urllib.parse.urlsplit(newurl)
        old = urllib.parse.urlsplit(self.original)
        # Never drain an unbounded redirect body. Close it before the one permitted
        # follow-up GET; do not log its possibly signed destination URL.
        fp.close()
        require(self.count == 1 and old.hostname == 'github.com' and u.scheme == 'https' and
                u.hostname == 'release-assets.githubusercontent.com' and not u.username and
                not u.password and u.port in (None, 443), 'redirect-refused')
        return self.parent.open(urllib.request.Request(newurl, headers={'User-Agent': 'fixed-public-packaging/1'}),
                                timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def download(start):
    spec = load(INPUTS / 'ACQUISITION.json')
    require(len(spec['assets']) == 5, 'download-count')
    total = 0
    receipt = []
    for asset in spec['assets']:
        require(time.monotonic() < start + 155 and asset['max_downloads'] == 1, 'acquisition-time')
        u = urllib.parse.urlsplit(asset['url'])
        require(u.scheme == 'https' and u.hostname in ('github.com', 'files.pythonhosted.org') and
                not u.username and not u.password and u.port in (None, 443), 'asset-url')
        name = asset['filename']
        require(safe(name).name == name, 'asset-name')
        target = WORK / ('downloads' if name.endswith('.tar.gz') else 'wheels') / name
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect(asset['url']))
        h = hashlib.sha256()
        count = 0
        # No auth/cookie handlers, proxy environment, retry, index or fallback route.
        with opener.open(urllib.request.Request(asset['url'], headers={'User-Agent': 'fixed-public-packaging/1'}),
                         timeout=min(20, max(1, start + 155 - time.monotonic()))) as response, target.open('xb') as out:
            require(response.status == 200, 'asset-status')
            while True:
                require(time.monotonic() < start + 155, 'acquisition-time')
                data = response.read(65536)
                if not data:
                    break
                count += len(data)
                total += len(data)
                require(count <= asset['bytes'] and total <= spec['response_body_cap_bytes'], 'download-byte-limit')
                h.update(data)
                out.write(data)
        require(count == asset['bytes'] and h.hexdigest() == asset['sha256'], 'asset-pin')
        target.chmod(0o444)
        receipt.append({'filename': name, 'bytes': count, 'sha256': h.hexdigest()})
    # Acquisition does not write the offline receipt directory.
    (WORK / 'downloads/acquisition.json').write_text(json.dumps(receipt, sort_keys=True))


def extract_python(path, target):
    """Manual extraction. Links must resolve within the new owned toolchain."""
    seen, links, expanded = set(), [], 0
    with tarfile.open(path, 'r:gz') as archive:
        for count, item in enumerate(archive, 1):
            require(count <= 16000, 'python-member-count')
            name = item.name.rstrip('/')
            while name.startswith('./'):
                name = name[2:]
            if not name and item.isdir():
                continue
            rel = safe(name)
            require(str(rel) not in seen, 'python-duplicate')
            seen.add(str(rel))
            dest = target.joinpath(*rel.parts)
            require(all(not x.is_symlink() for x in dest.parents if x.is_relative_to(target)), 'python-link-parent')
            if item.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif item.isfile():
                expanded += item.size
                require(item.size <= 128 * 1024**2 and expanded <= 512 * 1024**2, 'python-expanded-limit')
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as src, dest.open('xb') as out:
                    shutil.copyfileobj(src, out, 65536)
                dest.chmod(0o755 if item.mode & 0o111 else 0o644)
            elif item.issym() or item.islnk():
                links.append((rel, item.linkname, item.issym()))
            else:
                raise RuntimeError('python-special-member')
    # Resolve all links lexically and by realpath before creation; no absolute links.
    for rel, value, symbolic in links:
        require(value and not value.startswith('/') and '\\' not in value, 'python-link-target')
        dest = target.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        link = (dest.parent / value) if symbolic else (target / value)
        resolved = link.resolve()
        require(resolved.is_relative_to(target.resolve()) and resolved.exists() and
                all(not x.is_symlink() for x in dest.parents if x.is_relative_to(target)), 'python-link-escape')
        if symbolic:
            dest.symlink_to(value)
        else:
            require(resolved.is_file(), 'python-hardlink-type')
            os.link(resolved, dest)


def bootstrap(start):
    spec = load(INPUTS / 'ACQUISITION.json')
    for a in spec['assets']:
        path = WORK / ('downloads' if a['filename'].endswith('.tar.gz') else 'wheels') / a['filename']
        require(path.stat().st_size == a['bytes'] and sha(path) == a['sha256'], 'bootstrap-input-pin')
    extract_python(WORK / 'downloads' / spec['assets'][0]['filename'], WORK / 'python')
    require((WORK / 'python/bin/python3.12').is_file(), 'toolchain-layout')
    cmds = load(INPUTS / 'COMMANDS.json')['commands']
    results = [command(x, start + 180) for x in cmds if x['phase'] == 'toolchain-admission']
    # The exact interpreter/version/prefix and installed build versions are admission, not assumptions.
    probe = {'id': 'toolchain-identity', 'argv': ['/work/builder/bin/python', '-I', '-c',
        "import sys,sysconfig,platform,pathlib,ensurepip,pip,importlib.metadata as m; assert sys.version_info[:3]==(3,12,15); assert sys.base_prefix=='/work/python'; assert sys.prefix=='/work/builder'; assert platform.machine()=='x86_64'; assert sys.abiflags=='' and sys.implementation.cache_tag=='cpython-312' and sysconfig.get_config_var('SOABI')=='cpython-312-x86_64-linux-gnu'; assert all(any(pathlib.Path(p).resolve().is_relative_to(pathlib.Path(root)) for root in ('/work/python','/work/builder')) for p in sys.path); assert pip.__version__==ensurepip.version(); assert all(m.version(k)==v for k,v in {'build':'1.6.1','setuptools':'84.0.0','packaging':'26.3','pyproject_hooks':'1.3.3'}.items()); print(sys.version.split()[0],sysconfig.get_config_var('SOABI'),pip.__version__,ensurepip.version())"],
        'cwd': '/work/empty', 'max_invocations': 1, 'wall_seconds': 2}
    results.append(command(probe, start + 180))
    (WORK / 'receipts/bootstrap.json').write_text(json.dumps({'commands': results}, sort_keys=True))


def extract_sdist(manifest_path, name, archive_path, target):
    proposal = next(x for x in load(Path(manifest_path))['proposals'] if x['tool'] == name)
    prefix = proposal['runtime_package'] + '-' + proposal['stable_target']
    pins = {x['path']: x['prepared_stable_sha256'] for x in proposal['files']}
    rcpath = INPUTS / 'rc-archives' / (proposal['runtime_package'] + '-' + proposal['from'] + '.tar.gz')
    allowed = set()
    with tarfile.open(rcpath, 'r:gz') as rc:
        for member in rc:
            if member.isfile():
                allowed.add('/'.join(safe(member.name).parts[1:]))
    target = Path(target)
    require(not target.exists() and Path(archive_path).stat().st_size <= 8 * 1024**2, 'sdist-admission')
    members, expanded = {}, 0
    with tarfile.open(archive_path, 'r:gz') as archive:
        seen = set()
        for count, item in enumerate(archive, 1):
            require(count <= 128, 'sdist-member-count')
            rel = safe(item.name.rstrip('/'))
            require(rel.parts[0] == prefix and str(rel) not in seen, 'sdist-root-duplicate')
            seen.add(str(rel))
            require(item.isdir() or item.isfile(), 'sdist-special-link')
            if item.isdir():
                continue
            expanded += item.size
            require(expanded <= 16 * 1024**2, 'sdist-expanded-limit')
            key = '/'.join(rel.parts[1:])
            data = archive.extractfile(item).read()
            require(key in allowed and (key not in pins or hashlib.sha256(data).hexdigest() == pins[key]), 'sdist-source-binding')
            members[key] = data
    require(set(members) == allowed and set(pins).intersection(allowed) <= set(members), 'sdist-membership')
    target.mkdir()
    for rel, data in members.items():
        dest = target / prefix / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as stream:
            stream.write(data)


def fixtures(source, target):
    source, target = Path(source), Path(target)
    require(not any(target.iterdir()), 'fixture-existing')
    for tool in ('task-handoff-check', 'delivery-evidence-check'):
        shutil.copytree(source / tool / 'examples', target / tool, symlinks=False)
    (target / 'malformed.json').write_bytes(b'{')


def package(start):
    allcmds = load(INPUTS / 'COMMANDS.json')['commands']
    results = load(WORK / 'receipts/bootstrap.json')['commands']
    total_output = 0
    for item in allcmds:
        if item['phase'] != 'offline':
            continue
        if item['id'] == 'delivery-evidence-check-tampered':
            with (WORK / 'fixtures/delivery-evidence-check/artifacts/review.txt').open('ab') as stream:
                stream.write(b'tamper')
        result = command(item, start + 480)
        total_output += result['stdout_bytes'] + result['stderr_bytes']
        require(total_output <= 1024**2, 'aggregate-command-output')
        results.append(result)
    binding = load(WORK / 'receipts/artifact-binding.json')
    receipt = {'schema': 1, 'status': 'PACKAGING_AND_CONSUMER_CHECKS_PASS', 'published': False,
               'full_suites_rerun': False, 'commands': results, 'artifact_binding': binding,
               'acquisition': load(WORK / 'downloads/acquisition.json'),
               'scope': 'metadata binding and six installed structural/integrity CLI cases; no authenticity claim'}
    raw = (json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n').encode()
    require(len(raw) <= 65536, 'receipt-size')
    with (WORK / 'receipts/packaging-receipt.json').open('xb') as stream:
        stream.write(raw)


def final_check(start):
    require(time.monotonic() < start + 480, 'final-deadline')
    cmds = load(INPUTS / 'COMMANDS.json')['commands']
    item = next(x for x in cmds if x['phase'] == 'final')
    result = command(item, min(start + 480, float(os.environ['PACKAGING_PHASE_DEADLINE'])))
    fresh = load(WORK / 'receipts/final-binding.json')
    receipt = load(WORK / 'receipts/packaging-receipt.json')
    expected = ([x['id'] for x in cmds if x['phase'] == 'toolchain-admission'] +
                ['toolchain-identity'] + [x['id'] for x in cmds if x['phase'] == 'offline'])
    actual = [x['id'] for x in receipt['commands']]
    require(actual == expected, 'command-receipt-binding')
    require(receipt['published'] is False and receipt['status'] == 'PACKAGING_AND_CONSUMER_CHECKS_PASS', 'receipt-status')
    require(fresh == receipt['artifact_binding'], 'fresh-binding-delta')
    manifest = load(INPUTS / 'STABLE-SOURCE-INPUT-MANIFEST.json')
    expected_paths = {str(WORK / 'dist' / p['tool'] / f) for p in manifest['proposals'] for f in p['expected_artifacts']}
    actual_paths = {a['path'] for t in fresh['tools'] for a in t['artifacts']}
    require(actual_paths == expected_paths, 'fixed-artifact-map')
    for tool in fresh['tools']:
        for artifact in tool['artifacts']:
            path = Path(artifact['path'])
            require(str(path) in expected_paths and not path.is_symlink() and path.stat().st_size == artifact['bytes'] and
                    sha(path) == artifact['sha256'], 'final-artifact-drift')
    receipt['commands'].append(result)
    receipt['fresh_readonly_validation'] = True
    raw = (json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n').encode()
    require(len(raw) <= 65536, 'final-receipt-limit')
    # Only this fresh phase writes receipts; package descendants are already dead.
    (WORK / 'receipts/packaging-receipt.json').write_bytes(raw)


def admission():
    require(os.getuid() == os.geteuid() == 10001 and os.getgid() == os.getegid() == 10001, 'worker-identity')
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(4, 0, 0, 0, 0) == 0 and libc.prctl(3, 0, 0, 0, 0) == 0, 'nondumpable-orchestrator')
    status = Path('/proc/self/status').read_text()
    for field in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        require(int(re.search(r'^' + field + r':\s*(\w+)', status, re.M)[1], 16) == 0, 'worker-capability')
    require('NoNewPrivs:\t1' in status, 'worker-nnp')
    parent = json.loads(os.environ['PACKAGING_PARENT_NAMESPACES'])
    for name in ('net', 'mnt', 'pid'):
        same = os.readlink('/proc/self/ns/' + name) == parent[name]
        require(same == (name == 'net' and os.environ['PACKAGING_NETWORK_PHASE'] == '1'), 'namespace-identity')
    mounts = {line.split()[4]: set(line.split()[5].split(',')) for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    require('ro' in mounts['/'] and 'ro' in mounts['/inputs'] and 'ro' in mounts['/control'], 'readonly-control')
    require(Path('/control/payload.py').is_file() and os.access('/control/payload.py', os.R_OK), 'control-readability')
    phase = sys.argv[1]
    mutable = {'downloads', 'wheels'} if phase == 'download' else (
        {'python', 'builder', 'tmp', 'receipts'} if phase == 'bootstrap' else
        {'receipts', 'tmp'} if phase == 'final-check' else {'src', 'sdist', 'dist', 'fixtures', 'receipts', 'consumers', 'tmp'})
    for name in ('downloads', 'python', 'wheels', 'builder', 'src', 'sdist', 'dist', 'fixtures', 'receipts', 'consumers', 'tmp', 'empty'):
        require(('rw' if name in mutable else 'ro') in mounts['/work/' + name], 'work-mount-mode')
    require('rw' in mounts['/tmp'] and os.stat('/tmp').st_ino == os.stat('/work/tmp').st_ino and
            os.stat('/tmp').st_dev == os.stat('/work/tmp').st_dev, 'tmp-alias')
    require(time.monotonic() < float(os.environ['PACKAGING_PHASE_DEADLINE']), 'phase-admission-time')


if __name__ == '__main__':
    role = sys.argv[1]
    admission()
    if role == 'extract-sdist':
        extract_sdist(*sys.argv[2:])
    elif role == 'fixtures':
        fixtures(*sys.argv[2:])
    else:
        {'download': download, 'bootstrap': bootstrap, 'package': package, 'final-check': final_check}[role](float(sys.argv[2]))
