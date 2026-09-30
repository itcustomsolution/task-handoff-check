"""Command line interface for handoff consistency checking."""
import argparse
import json
from pathlib import Path
from . import __version__, HandoffError, load_handoff, validate_handoff


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check handoff consistency without authorizing or performing work.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('handoff', type=Path)
    parser.add_argument('--json', action='store_true', help='print a machine-readable report')
    parser.add_argument('--as-of', help='explicit timestamp for comparison; no implicit current time')
    parser.add_argument('--max-age-hours', type=float, help='maximum age; requires --as-of')
    args = parser.parse_args(argv)
    options = {'as_of': args.as_of, 'max_age_hours': args.max_age_hours}
    try:
        validate_handoff({}, **options)
    except HandoffError as exc:
        parser.error(str(exc))
    try:
        result = validate_handoff(load_handoff(args.handoff), **options)
    except HandoffError as exc:
        result = {'ok': False, 'scope': 'handoff_consistency', 'freshness': 'not_evaluated',
                  'issues': [{'code': 'input_error', 'location': '$', 'message': str(exc)}]}
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(('PASS' if result['ok'] else 'FAIL') + ': handoff consistency; freshness ' + result['freshness'] + '.')
        for issue in result['issues']:
            print(f"- {issue['location']}: {issue['code']}: {issue['message']}")
        print('This checks assertions, not truth, authorization, file contents, or task completion.')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
