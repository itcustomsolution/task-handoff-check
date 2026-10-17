"""Pure consistency validation; references are never opened or fetched."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import unicodedata
from urllib.parse import urlsplit

MAX_HANDOFF_BYTES = 1024 * 1024
PLACEHOLDERS = {'tbd', 'todo', 'unknown', 'unassigned', 'n/a', 'none', '-', '?'}
REQUIRED = {'schema_version', 'objective', 'owner', 'status', 'next_action',
            'inputs', 'evidence', 'blockers', 'completion_criteria'}


class HandoffError(ValueError):
    """Input or freshness options could not be processed."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError('duplicate JSON object key')
        result[key] = value
    return result


def _constant(value):
    raise HandoffError('nonfinite JSON numbers are not supported')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise HandoffError('nonfinite JSON numbers are not supported')
    return number


def load_handoff(path):
    """Read one regular UTF-8 JSON file, up to 1 MiB. Reject final symlinks."""
    path = Path(path)
    if '\x00' in str(path):
        raise HandoffError('handoff path is invalid (contains a NUL character)')
    fd = None
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise HandoffError('handoff must be a regular file, not a symlink')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise HandoffError('handoff must be a regular file')
        stream = os.fdopen(fd, 'rb')
        fd = None
        with stream:
            contents = stream.read(MAX_HANDOFF_BYTES + 1)
        if len(contents) > MAX_HANDOFF_BYTES:
            raise HandoffError('handoff exceeds 1 MiB')
        return json.loads(contents.decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=_constant, parse_float=_finite_float)
    except HandoffError:
        raise
    except UnicodeDecodeError as exc:
        raise HandoffError('handoff must contain valid UTF-8 JSON') from exc
    except json.JSONDecodeError as exc:
        raise HandoffError(f'invalid JSON at line {exc.lineno}, column {exc.colno}') from exc
    except RecursionError as exc:
        raise HandoffError('handoff JSON nesting is too deep') from exc
    except OSError as exc:
        raise HandoffError('cannot read handoff JSON: ' + (exc.strerror or 'file unavailable')) from exc
    except ValueError as exc:
        raise HandoffError('handoff contains an unsupported JSON value') from exc
    finally:
        if fd is not None:
            os.close(fd)


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value):
        raise HandoffError('timestamp must include date, time, seconds and a timezone')
    try:
        # Explicit offset validation avoids datetime normalizing e.g. +00:60.
        if value[-1] != 'Z' and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError('invalid timezone offset')
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise HandoffError('timestamp has an invalid date, time or offset') from exc


def _portable_path(value):
    if not isinstance(value, str) or not value:
        return False
    for part in value.split('/'):
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(c in '<>:"\\|?*' or unicodedata.category(c).startswith('C') for c in part)
                or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])', part.split('.')[0])):
            return False
    return True


def _http_url(value):
    if not isinstance(value, str) or any(c.isspace() or unicodedata.category(c).startswith('C') for c in value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme in ('http', 'https') and bool(url.hostname) and url.username is None
                and url.password is None and (url.port is None or 0 < url.port <= 65535)
                and '\\' not in value)
    except ValueError:
        return False


def validate_handoff(data, *, as_of=None, max_age_hours=None):
    """Return a JSON-compatible report. Optional freshness uses only supplied time.

    ``as_of`` is a timestamp string. ``max_age_hours`` is a finite nonnegative
    int or float and requires ``as_of``; booleans are not numbers here. Integers
    of any size are compared exactly, never converted to float. Bad options
    raise HandoffError; invalid document fields appear in the returned issues list.
    """
    clock = _timestamp(as_of) if as_of is not None else None
    if max_age_hours is not None:
        # ponytail: no upper cap. The old OverflowError came from math.isfinite() on a huge int;
        # Python compares int and float exactly, and any age beyond the timestamp range means "no limit".
        if (clock is None or type(max_age_hours) not in (int, float)
                or (type(max_age_hours) is float and not math.isfinite(max_age_hours))
                or max_age_hours < 0):
            raise HandoffError('max_age_hours requires as_of and a finite nonnegative number')
    issues = []
    freshness = 'not_evaluated'

    def issue(code, location, message):
        issues.append({'code': code, 'location': location, 'message': message})

    def text(value, location):
        if (not isinstance(value, str) or not value.strip() or value.strip().casefold() in PLACEHOLDERS
                or any(unicodedata.category(c).startswith('C') for c in value)):
            issue('missing_text', location, 'use meaningful nonempty text without placeholders or control characters')
            return False
        return True

    def result():
        return {'ok': not issues, 'scope': 'handoff_consistency', 'freshness': freshness, 'issues': issues}

    if not isinstance(data, dict):
        issue('invalid_schema', '$', 'handoff must be an object')
        return result()
    if REQUIRED - set(data):
        issue('invalid_schema', '$', 'missing fields: ' + ', '.join(sorted(REQUIRED - set(data))))
    if set(data) - REQUIRED - {'updated_at'}:
        issue('unknown_field', '$', 'unknown handoff field')
    if type(data.get('schema_version')) is not int or data.get('schema_version') != 1:
        issue('invalid_schema', 'schema_version', 'schema_version must be integer 1')
    text(data.get('objective'), 'objective')
    text(data.get('owner'), 'owner')
    status = data.get('status')
    if status not in ('ready', 'in_progress', 'blocked', 'complete'):
        issue('invalid_schema', 'status', 'status must be ready, in_progress, blocked or complete')
    if status == 'complete':
        if data.get('next_action') is not None:
            issue('status_conflict', 'next_action', 'complete requires a null next_action')
    else:
        text(data.get('next_action'), 'next_action')

    evidence_labels = set()
    for field in ('inputs', 'evidence'):
        references = data.get(field)
        if not isinstance(references, list):
            issue('invalid_schema', field, 'references must be an array')
            continue
        labels = set()
        for index, ref in enumerate(references):
            location = f'{field}[{index}]'
            if not isinstance(ref, dict) or set(ref) not in ({'label', 'path'}, {'label', 'url'}):
                issue('invalid_reference', location, 'reference requires label and exactly one path or url')
                continue
            label = ref['label']
            if text(label, location + '.label'):
                key = unicodedata.normalize('NFC', label.strip()).casefold()
                if key in labels:
                    issue('duplicate_label', location + '.label', 'labels must be unique within this array')
                labels.add(key)
                if field == 'evidence':
                    evidence_labels.add(label)
            if 'path' in ref and not _portable_path(ref['path']):
                issue('invalid_reference', location + '.path', 'path must be portable and relative, without traversal')
            if 'url' in ref and not _http_url(ref['url']):
                issue('invalid_reference', location + '.url', 'url must be absolute HTTP(S), without credentials')

    blockers = data.get('blockers')
    if not isinstance(blockers, list):
        issue('invalid_schema', 'blockers', 'blockers must be an array')
    else:
        if (status == 'blocked') != bool(blockers):
            issue('status_conflict', 'blockers', 'open blockers require blocked status; blocked requires an open blocker')
        for index, blocker in enumerate(blockers):
            location = f'blockers[{index}]'
            if not isinstance(blocker, dict) or set(blocker) != {'description', 'owner'}:
                issue('invalid_schema', location, 'blocker requires description and owner')
                continue
            text(blocker['description'], location + '.description')
            text(blocker['owner'], location + '.owner')

    criteria = data.get('completion_criteria')
    if not isinstance(criteria, list) or not criteria:
        issue('invalid_schema', 'completion_criteria', 'completion_criteria must be a nonempty array')
    else:
        descriptions = set()
        for index, criterion in enumerate(criteria):
            location = f'completion_criteria[{index}]'
            if not isinstance(criterion, dict) or set(criterion) != {'description', 'met', 'evidence'}:
                issue('invalid_schema', location, 'criterion requires description, met and evidence')
                continue
            if text(criterion['description'], location + '.description'):
                key = unicodedata.normalize('NFC', criterion['description'].strip()).casefold()
                if key in descriptions:
                    issue('duplicate_criterion', location, 'completion criteria must be distinct')
                descriptions.add(key)
            if type(criterion['met']) is not bool:
                issue('invalid_schema', location + '.met', 'met must be a boolean')
            elif status == 'complete' and not criterion['met']:
                issue('incomplete_criterion', location + '.met', 'complete requires every criterion to be met')
            evidence = criterion['evidence']
            if not isinstance(evidence, list) or not all(isinstance(label, str) for label in evidence):
                issue('invalid_schema', location + '.evidence', 'evidence must be an array of evidence labels')
            else:
                if criterion['met'] is True and not evidence:
                    issue('missing_evidence', location + '.evidence', 'a met criterion requires an evidence reference')
                if len(set(evidence)) != len(evidence):
                    issue('invalid_schema', location + '.evidence', 'evidence labels must be unique')
                if any(label not in evidence_labels for label in evidence):
                    issue('unknown_evidence', location + '.evidence', 'unknown evidence label; use exact label spelling')

    updated = None
    if 'updated_at' in data:
        try:
            updated = _timestamp(data['updated_at'])
        except HandoffError as exc:
            issue('invalid_timestamp', 'updated_at', str(exc))
    if clock is not None:
        if updated is None:
            freshness = 'unavailable'
            issue('missing_timestamp', 'updated_at', 'a valid updated_at is required when as_of is supplied')
        else:
            age_hours = (clock - updated).total_seconds() / 3600
            freshness = 'timestamp_compared'
            if age_hours < 0:
                freshness = 'future'
                issue('future_timestamp', 'updated_at', 'updated_at is after the supplied as_of')
            elif max_age_hours is not None:
                freshness = 'within_limit' if age_hours <= max_age_hours else 'stale'
                if freshness == 'stale':
                    issue('stale', 'updated_at', 'updated_at exceeds the supplied maximum age')
    return result()
