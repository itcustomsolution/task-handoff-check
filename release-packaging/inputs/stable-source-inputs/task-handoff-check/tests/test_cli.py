"""Synthetic handoffs exercise observable CLI behavior."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def handoff():
    return {'schema_version': 1, 'objective': 'Review the sample release notes',
            'owner': 'Sample Maintainer', 'status': 'in_progress',
            'next_action': 'Compare the notes with the sample changelog',
            'inputs': [{'label': 'changelog', 'path': 'docs/changelog.md'}],
            'evidence': [], 'blockers': [],
            'completion_criteria': [{'description': 'All sample changes reviewed', 'met': False, 'evidence': []}]}


class HandoffCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'handoff.json'
        self.data = handoff()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'task_handoff_check', *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, timeout=10)

    def check(self, *args):
        self.path.write_text(json.dumps(self.data))
        return self.run_cli(self.path, '--json', *args)

    def assert_invalid(self, result, code=None):
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertTrue(result.stdout.startswith('{'), result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data['ok'])
        if code:
            self.assertIn(code, [x['code'] for x in data['issues']])
        return data

    def complete(self):
        self.data.update(status='complete', next_action=None)
        self.data['evidence'] = [{'label': 'review', 'path': 'reports/review.txt'}]
        self.data['completion_criteria'][0].update(met=True, evidence=['review'])

    def test_valid_handoff_does_not_open_referenced_files(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertEqual(json.loads(result.stdout)['freshness'], 'not_evaluated')

    def test_complete_requires_met_criteria_and_evidence(self):
        self.complete()
        self.assertEqual(self.check().returncode, 0)
        self.data['completion_criteria'][0]['met'] = False
        self.assert_invalid(self.check(), 'incomplete_criterion')

    def test_met_criterion_needs_evidence_label(self):
        self.data['completion_criteria'][0]['met'] = True
        self.assert_invalid(self.check(), 'missing_evidence')
        self.data['completion_criteria'][0]['evidence'] = ['absent']
        self.assert_invalid(self.check(), 'unknown_evidence')

    def test_complete_cannot_have_blockers_or_next_action(self):
        self.complete()
        self.data['blockers'] = [{'description': 'Review unavailable', 'owner': 'Sample Reviewer'}]
        self.assert_invalid(self.check(), 'status_conflict')
        self.data['blockers'] = []
        self.data['next_action'] = 'More work remains'
        self.assert_invalid(self.check(), 'status_conflict')

    def test_blocked_requires_blocker_and_action(self):
        self.data['status'] = 'blocked'
        self.assert_invalid(self.check(), 'status_conflict')
        self.data['blockers'] = [{'description': 'Waiting for sample', 'owner': 'Sample Reviewer'}]
        self.assertEqual(self.check().returncode, 0)
        self.data['next_action'] = None
        self.assert_invalid(self.check(), 'missing_text')

    def test_active_status_cannot_have_open_blockers(self):
        self.data['blockers'] = [{'description': 'Sample is missing', 'owner': 'Sample Reviewer'}]
        self.assert_invalid(self.check(), 'status_conflict')

    def test_empty_and_placeholder_fields_are_rejected(self):
        for field in ('objective', 'owner', 'next_action'):
            for value in ('', ' ', 'TBD', 'unknown', None):
                with self.subTest(field=field, value=value):
                    self.data = handoff()
                    self.data[field] = value
                    self.assert_invalid(self.check(), 'missing_text')

    def test_rejects_invalid_schema_values_and_unknown_fields(self):
        for value in ([], {}, {**handoff(), 'schema_version': True},
                      {**handoff(), 'status': 'done'}, {**handoff(), 'extra': True},
                      {**handoff(), 'completion_criteria': []},
                      {**handoff(), 'completion_criteria': [None]},
                      {**handoff(), 'blockers': 'none'}, {**handoff(), 'inputs': [None]}):
            with self.subTest(value=value):
                self.data = value
                self.assert_invalid(self.check())

    def test_criterion_met_is_a_boolean(self):
        for value in ('true', 1, None):
            self.data['completion_criteria'][0]['met'] = value
            self.assert_invalid(self.check(), 'invalid_schema')

    def test_references_need_one_path_or_http_link(self):
        good = {'label': 'reference', 'url': 'https://example.invalid/guide'}
        self.data['inputs'] = [good]
        self.assertEqual(self.check().returncode, 0)
        for value in ({**good, 'path': 'file.txt'}, {'label': 'reference'},
                      {**good, 'url': 'javascript:alert(1)'},
                      {**good, 'url': 'https://user:secret@example.invalid/'},
                      {**good, 'url': 'https://example.invalid:bad/'},
                      {**good, 'url': 'https://exa mple.invalid/'},
                      {'label': 'reference', 'path': '../private'},
                      {'label': 'reference', 'path': '/absolute'},
                      {'label': 'reference', 'path': 'NUL'}):
            with self.subTest(value=value):
                self.data['inputs'] = [value]
                self.assert_invalid(self.check(), 'invalid_reference')

    def test_repeated_labels_are_ambiguous(self):
        self.data['evidence'] = [{'label': 'review', 'path': 'review.txt'},
                                 {'label': 'REVIEW', 'path': 'other.txt'}]
        self.assert_invalid(self.check(), 'duplicate_label')

    def test_repeated_criteria_are_rejected(self):
        self.data['completion_criteria'].append(copy.deepcopy(self.data['completion_criteria'][0]))
        self.assert_invalid(self.check(), 'duplicate_criterion')

    def test_timestamp_requires_timezone_and_valid_calendar(self):
        for value in ('2026-01-01', '2026-01-01T12:00:00', '2026-02-30T00:00:00Z', 123):
            self.data['updated_at'] = value
            self.assert_invalid(self.check(), 'invalid_timestamp')

    def test_old_timestamp_is_not_stale_without_explicit_as_of(self):
        self.data['updated_at'] = '2000-01-01T00:00:00Z'
        result = self.check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['freshness'], 'not_evaluated')

    def test_supplied_as_of_checks_future_and_stale(self):
        self.data['updated_at'] = '2026-01-01T00:00:00Z'
        self.assert_invalid(self.check('--as-of', '2025-12-31T00:00:00Z'), 'future_timestamp')
        result = self.check('--as-of', '2026-01-02T00:00:00Z', '--max-age-hours', '24')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['freshness'], 'within_limit')
        self.assert_invalid(self.check('--as-of', '2026-01-02T00:00:01Z', '--max-age-hours', '24'), 'stale')

    def test_timezones_compare_same_instant(self):
        self.data['updated_at'] = '2026-01-01T01:00:00+01:00'
        self.assertEqual(self.check('--as-of', '2026-01-01T00:00:00Z', '--max-age-hours', '0').returncode, 0)

    def test_freshness_request_requires_timestamp(self):
        self.assert_invalid(self.check('--as-of', '2026-01-02T00:00:00Z', '--max-age-hours', '24'), 'missing_timestamp')

    def test_invalid_cli_time_options_are_usage_errors(self):
        for args in (('--max-age-hours', '24'), ('--as-of', 'bad'),
                     ('--as-of', '2026-01-01T00:00:00Z', '--max-age-hours', 'nan'),
                     ('--as-of', '2026-01-01T00:00:00Z', '--max-age-hours', '-1')):
            self.assertEqual(self.check(*args).returncode, 2)

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for value in ('{"owner":"one","owner":"two"}', '{"x":NaN}', '{'):
            self.path.write_text(value)
            self.assert_invalid(self.run_cli(self.path, '--json'), 'input_error')

    def test_oversized_input_is_rejected(self):
        self.path.write_bytes(b' ' * (1024 * 1024 + 1))
        self.assert_invalid(self.run_cli(self.path, '--json'), 'input_error')

    def test_human_output_and_version(self):
        self.check()
        result = self.run_cli(self.path)
        self.assertEqual(result.returncode, 0)
        self.assertIn('consistency', result.stdout.lower())
        # Release-candidate versions differ from 0.1.0: compare with the package metadata, not a literal.
        from task_handoff_check import __version__
        self.assertIn(f'version = "{__version__}"', (ROOT / 'pyproject.toml').read_text())
        self.assertEqual(self.run_cli('--version').stdout.strip(), __version__)


if __name__ == '__main__':
    unittest.main()
