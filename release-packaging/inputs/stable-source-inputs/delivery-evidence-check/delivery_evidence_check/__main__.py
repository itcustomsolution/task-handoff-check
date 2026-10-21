"""Command line interface."""
import argparse
import json
from pathlib import Path
import sys
from . import __version__, EvidenceError, create_manifest, load_manifest, verify_manifest


def _byte_limit(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('byte limit must be a nonnegative integer') from exc
    if number < 0:
        raise argparse.ArgumentTypeError('byte limit must be a nonnegative integer')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check local evidence integrity, not truth or completion.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='hash explicitly named files and print a manifest')
    create.add_argument('--root', type=Path, default=Path('.'))
    create.add_argument('paths', nargs='+')
    verify = commands.add_parser('verify', help='read and compare a manifest')
    verify.add_argument('manifest', type=Path)
    verify.add_argument('--root', type=Path, help='file root; defaults to the manifest directory')
    verify.add_argument('--json', action='store_true', help='print a machine-readable report')
    for command in (create, verify):
        command.add_argument('--max-file-bytes', type=_byte_limit,
                             help='optional maximum bytes per evidence file')
        command.add_argument('--max-total-bytes', type=_byte_limit,
                             help='optional total evidence-byte budget for this invocation')
    args = parser.parse_args(argv)
    limits = {'max_file_bytes': args.max_file_bytes, 'max_total_bytes': args.max_total_bytes}
    if args.command == 'create':
        try:
            manifest = create_manifest(args.root, args.paths, **limits)
        except EvidenceError as exc:
            print('error: ' + str(exc), file=sys.stderr)
            return 1
        print(json.dumps(manifest, indent=2, ensure_ascii=True))
        return 0
    try:
        manifest = load_manifest(args.manifest)
        result = verify_manifest(manifest, args.root if args.root is not None else args.manifest.parent, **limits)
    except EvidenceError as exc:
        result = {'ok': False, 'scope': 'local_file_integrity', 'files_checked': 0, 'claimed_checks': 0,
                  'issues': [{'code': 'input_error', 'location': '$', 'message': str(exc)}]}
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(('PASS' if result['ok'] else 'FAIL') + ': local file integrity; '
              + str(result['files_checked']) + ' file(s) read.')
        for issue in result['issues']:
            print(f"- {issue['location']}: {issue['code']}: {issue['message']}")
        print('Matching hashes do not prove truth, authenticity, ownership, authorization, test success, or task completion.')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
