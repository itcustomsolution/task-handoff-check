"""Manifest validation and local hashing; no commands or network operations."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import unicodedata

MAX_MANIFEST_BYTES = 1024 * 1024


class EvidenceError(ValueError):
    """An input or local file cannot safely be processed."""


class _LimitExceeded(EvidenceError):
    """An explicitly configured hashing budget was exceeded."""


class _ReadBudget:
    def __init__(self, max_file_bytes=None, max_total_bytes=None):
        for name, value in (('max_file_bytes', max_file_bytes), ('max_total_bytes', max_total_bytes)):
            if value is not None and (type(value) is not int or value < 0):
                raise EvidenceError(name + ' must be a nonnegative integer or None')
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.total = 0

    def check(self, file_bytes):
        if self.max_file_bytes is not None and file_bytes > self.max_file_bytes:
            raise _LimitExceeded('evidence file exceeds max_file_bytes')
        if self.max_total_bytes is not None and self.total > self.max_total_bytes:
            raise _LimitExceeded('evidence reads exceed max_total_bytes')

    def check_size(self, size):
        if self.max_file_bytes is not None and size > self.max_file_bytes:
            raise _LimitExceeded('evidence file exceeds max_file_bytes')
        if self.max_total_bytes is not None and size > self.max_total_bytes - self.total:
            raise _LimitExceeded('evidence file exceeds remaining max_total_bytes')

    def chunk_size(self, file_bytes):
        limits = [1024 * 1024]
        if self.max_file_bytes is not None:
            limits.append(max(1, self.max_file_bytes - file_bytes + 1))
        if self.max_total_bytes is not None:
            limits.append(max(1, self.max_total_bytes - self.total + 1))
        return min(limits)


def _require_path(value, name):
    if not isinstance(value, (str, os.PathLike)):
        raise EvidenceError(name + ' must be a string or path-like object')


def _path_parts(value):
    if not isinstance(value, str) or not value:
        raise EvidenceError('path must be a nonempty relative string')
    parts = value.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(c in '<>:"\\|?*' or unicodedata.category(c).startswith('C') for c in part)
                or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])', part.split('.')[0])):
            raise EvidenceError('path must use portable relative components without traversal')
    return parts


@contextmanager
def _open_regular(path):
    """Open a file without following symlinks, including ancestor components.

    POSIX uses directory descriptors to narrow path-swap races. The fallback
    checks every component but is not a sandbox against concurrent path changes.
    """
    _require_path(path, 'path')
    if '\x00' in str(path):
        raise EvidenceError('path contains a NUL character')
    path = Path(path).absolute()
    if '..' in path.parts:
        raise EvidenceError('parent traversal is not allowed in filesystem paths')
    fd = None
    directory_fd = None
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'):
            directory_fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            for part in path.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            fd = os.open(path.name, flags, dir_fd=directory_fd)
        else:
            for component in (*reversed(path.parents), path):
                mode = component.lstat().st_mode
                if stat.S_ISLNK(mode) or getattr(component, 'is_junction', lambda: False)():
                    raise EvidenceError('symlinks and junctions are not supported')
            if not stat.S_ISREG(path.lstat().st_mode):
                raise EvidenceError('expected a regular file')
            fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EvidenceError('expected a regular file')
        # Unbuffered: a buffered reader would prefetch past the byte budget from a file that grows (DEC-R1).
        stream = os.fdopen(fd, 'rb', buffering=0)
        fd = None
        with stream:
            yield stream
    except OSError as exc:
        raise EvidenceError('file unavailable, not regular, or a path component is a symlink') from exc
    finally:
        if fd is not None:
            os.close(fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _digest(root, relative, budget=None):
    parts = _path_parts(relative)
    budget = budget if budget is not None else _ReadBudget()
    with _open_regular(Path(root).joinpath(*parts)) as stream:
        before = os.fstat(stream.fileno())
        budget.check_size(before.st_size)
        digest = hashlib.sha256()
        file_bytes = 0
        while chunk := stream.read(budget.chunk_size(file_bytes)):
            file_bytes += len(chunk)
            budget.total += len(chunk)
            budget.check(file_bytes)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise EvidenceError('file changed while being read; retry with stable inputs')
        return digest.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError('duplicate JSON object key')
        result[key] = value
    return result


def _reject_constant(value):
    raise EvidenceError('nonfinite JSON numbers are not supported')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceError('nonfinite JSON numbers are not supported')
    return number


def load_manifest(path):
    """Read a strict UTF-8 JSON manifest of at most 1 MiB; reject symlinks."""
    with _open_regular(path) as stream:
        parts, size = [], 0
        while size <= MAX_MANIFEST_BYTES and (chunk := stream.read(MAX_MANIFEST_BYTES + 1 - size)):
            parts.append(chunk)
            size += len(chunk)
        contents = b''.join(parts)
    if len(contents) > MAX_MANIFEST_BYTES:
        raise EvidenceError('manifest exceeds 1 MiB')
    try:
        return json.loads(contents.decode('utf-8'), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant, parse_float=_finite_float)
    except EvidenceError:
        raise
    except UnicodeDecodeError as exc:
        raise EvidenceError('manifest must be valid UTF-8 JSON') from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f'invalid JSON at line {exc.lineno}, column {exc.colno}') from exc
    except RecursionError as exc:
        raise EvidenceError('manifest JSON nesting is too deep') from exc
    except ValueError as exc:  # e.g. an integer literal beyond the interpreter's digit limit
        raise EvidenceError('manifest contains an unsupported JSON value') from exc


def _validate(manifest):
    issues = []

    def issue(code, location, message):
        issues.append({'code': code, 'location': location, 'message': message})

    if not isinstance(manifest, dict):
        issue('invalid_schema', '$', 'manifest must be an object')
        return issues
    if set(manifest) - {'schema_version', 'files', 'local_checks'}:
        issue('unknown_field', '$', 'unknown manifest field')
    if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        issue('invalid_schema', 'schema_version', 'schema_version must be integer 1')
    files = manifest.get('files')
    if not isinstance(files, list) or not files:
        issue('invalid_schema', 'files', 'files must be a nonempty array')
        return issues
    known = set()
    portable_keys = set()
    for index, entry in enumerate(files):
        location = f'files[{index}]'
        if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
            issue('invalid_schema', location, 'file entry requires only path and sha256')
            continue
        path = entry['path']
        try:
            _path_parts(path)
        except EvidenceError as exc:
            issue('invalid_path', location + '.path', str(exc))
        else:
            key = unicodedata.normalize('NFC', path).casefold()
            if key in portable_keys:
                issue('duplicate_path', location + '.path', 'duplicate or case-colliding path')
            portable_keys.add(key)
            known.add(path)
        if not isinstance(entry['sha256'], str) or re.fullmatch('[0-9a-f]{64}', entry['sha256']) is None:
            issue('invalid_hash', location + '.sha256', 'sha256 must be 64 lowercase hexadecimal characters')
    checks = manifest.get('local_checks', [])
    if not isinstance(checks, list):
        issue('invalid_schema', 'local_checks', 'local_checks must be an array')
        return issues
    names = set()
    for index, entry in enumerate(checks):
        location = f'local_checks[{index}]'
        if not isinstance(entry, dict) or set(entry) != {'name', 'result', 'evidence'}:
            issue('invalid_schema', location, 'check requires only name, result and evidence')
            continue
        name = entry['name']
        if not isinstance(name, str) or not name.strip():
            issue('invalid_schema', location + '.name', 'check name must be nonempty')
        elif name.strip().casefold() in names:
            issue('invalid_schema', location + '.name', 'check names must be unique')
        else:
            names.add(name.strip().casefold())
        if entry['result'] not in ('passed', 'failed', 'not_run'):
            issue('invalid_schema', location + '.result', 'result must be passed, failed or not_run')
        evidence = entry['evidence']
        if not isinstance(evidence, list) or not all(isinstance(p, str) for p in evidence):
            issue('invalid_schema', location + '.evidence', 'evidence must be an array of listed paths')
        else:
            if entry['result'] in ('passed', 'failed') and not evidence:
                issue('invalid_schema', location + '.evidence', 'a claimed run requires evidence')
            if len(set(evidence)) != len(evidence):
                issue('invalid_schema', location + '.evidence', 'evidence paths must be unique')
            if any(p not in known for p in evidence):
                issue('unknown_evidence', location + '.evidence', 'evidence must refer to files in the manifest')
    return issues


def verify_manifest(manifest, root, *, max_file_bytes=None, max_total_bytes=None):
    """Return a JSON-compatible integrity report. Never execute claimed checks."""
    _require_path(root, 'root')
    budget = _ReadBudget(max_file_bytes, max_total_bytes)
    issues = _validate(manifest)
    checked = 0
    if not issues:
        for index, entry in enumerate(manifest['files']):
            location = f'files[{index}]'
            try:
                actual = _digest(root, entry['path'], budget)
            except _LimitExceeded as exc:
                issues.append({'code': 'resource_limit', 'location': location, 'message': str(exc)})
                continue
            except EvidenceError as exc:
                issues.append({'code': 'file_unavailable', 'location': location, 'message': str(exc)})
                continue
            checked += 1
            if actual != entry['sha256']:
                issues.append({'code': 'hash_mismatch', 'location': location, 'message': 'SHA-256 differs from manifest'})
    checks = manifest.get('local_checks', []) if isinstance(manifest, dict) else []
    return {'ok': not issues, 'scope': 'local_file_integrity', 'files_checked': checked,
            'claimed_checks': len(checks) if isinstance(checks, list) else 0, 'issues': issues}


def create_manifest(root, paths, *, max_file_bytes=None, max_total_bytes=None):
    """Hash explicit paths. Return a new manifest without writing it to disk."""
    _require_path(root, 'root')
    budget = _ReadBudget(max_file_bytes, max_total_bytes)
    if not paths:
        raise EvidenceError('at least one explicit file is required')
    manifest = {'schema_version': 1, 'files': [{'path': p, 'sha256': '0' * 64} for p in paths]}
    issues = _validate(manifest)
    if issues:
        raise EvidenceError(issues[0]['message'])
    for entry in manifest['files']:
        entry['sha256'] = _digest(root, entry['path'], budget)
    return manifest
