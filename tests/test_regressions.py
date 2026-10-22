"""Regressions for THC-1..THC-3 and the boundary between consistency and truth. Synthetic inputs only."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from task_handoff_check import HandoffError, load_handoff, validate_handoff
from test_cli import handoff

ROOT = Path(__file__).resolve().parents[1]
AS_OF = '2026-01-02T00:00:00Z'


class Reader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, raw, name='handoff.json'):
        path = Path(self.tmp.name) / name
        path.write_bytes(raw.encode() if isinstance(raw, str) else raw)
        return load_handoff(path)

    def test_thc2_exponent_overflow_is_rejected_everywhere(self):
        for raw in ('{"x": 1e999}', '{"x": -1e999}', '{"x": 1E+400}', '{"x": [0, {"y": 9e9999}]}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(HandoffError, 'nonfinite'):
                self.load(raw)
        for raw in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(HandoffError):
                self.load(raw)

    def test_finite_numbers_still_load(self):
        self.assertEqual(self.load('{"a": 1.5e308, "b": -0.0, "c": 1e-400, "d": 12345678901234567890}'),
                         {'a': 1.5e308, 'b': -0.0, 'c': 0.0, 'd': 12345678901234567890})

    def test_oversized_integer_literal_is_a_handoff_error_not_a_crash(self):
        with self.assertRaises(HandoffError):
            self.load('{"x": ' + '9' * 5000 + '}')

    def test_diagnostics_give_a_location_without_content_or_filename(self):
        with self.assertRaises(HandoffError) as caught:
            self.load('{"x": "SYNTHETIC_SECRET_SENTINEL" !}', name='SENTINEL_NAME.json')
        message = str(caught.exception)
        self.assertRegex(message, r'line 1, column \d+')
        self.assertNotIn('SENTINEL', message)
        with self.assertRaises(HandoffError) as caught:
            load_handoff(Path(self.tmp.name) / 'MISSING_SENTINEL.json')
        self.assertNotIn('SENTINEL', str(caught.exception))
        with self.assertRaisesRegex(HandoffError, 'UTF-8'):
            self.load(b'{"x": "\xff"}')
        # Deep nesting never escapes as RecursionError. Some interpreters (CPython 3.14.6 here) parse it
        # without a recursion limit; the schema check then refuses it.
        try:
            data = self.load('[' * 100000 + ']' * 100000)
        except HandoffError as exc:
            self.assertRegex(str(exc), 'too deep|unsupported|invalid')
        else:
            self.assertFalse(validate_handoff(data)['ok'])

    def test_thc_r2_nul_in_path_is_reported_as_an_invalid_path(self):
        with self.assertRaisesRegex(HandoffError, 'path is invalid'):
            load_handoff(Path(self.tmp.name) / 'a\x00b.json')


class Freshness(unittest.TestCase):
    def doc(self, updated='2026-01-01T00:00:00Z'):
        data = handoff()
        data['updated_at'] = updated
        return data

    def test_thc3_huge_finite_integers_are_accepted_exactly(self):
        for value in (10 ** 400, 2 ** 1024, 10 ** 20):
            with self.subTest(value=value):
                report = validate_handoff(self.doc(), as_of=AS_OF, max_age_hours=value)
                self.assertEqual(report['freshness'], 'within_limit')
        self.assertEqual(validate_handoff(self.doc(), as_of=AS_OF, max_age_hours=23)['freshness'], 'stale')
        self.assertEqual(validate_handoff(self.doc(), as_of=AS_OF, max_age_hours=24)['freshness'], 'within_limit')

    def test_rh01_long_intervals_are_compared_exactly(self):
        data = self.doc('1000-01-01T00:00:00Z')
        hours = 8993736  # exactly 1000-01-01 to 2026-01-01
        self.assertEqual(validate_handoff(data, as_of='2026-01-01T00:00:00Z', max_age_hours=hours)['freshness'], 'within_limit')
        report = validate_handoff(data, as_of='2026-01-01T00:00:00.000001Z', max_age_hours=hours)
        self.assertEqual((report['ok'], report['freshness']), (False, 'stale'))
        self.assertEqual(validate_handoff(data, as_of='2026-01-01T00:00:00.000001Z', max_age_hours=hours + 1)['freshness'], 'within_limit')
        short = self.doc('2026-01-01T00:00:00Z')
        self.assertEqual(validate_handoff(short, as_of='2026-01-01T00:30:00Z', max_age_hours=0.5)['freshness'], 'within_limit')
        self.assertEqual(validate_handoff(short, as_of='2026-01-01T00:30:00.000001Z', max_age_hours=0.5)['freshness'], 'stale')

    def test_thc3_invalid_options_raise_only_the_documented_error(self):
        for value in (float('inf'), float('-inf'), float('nan'), -1, -10 ** 400, True, False, '24', None.__class__):
            with self.subTest(value=value), self.assertRaises(HandoffError):
                validate_handoff(self.doc(), as_of=AS_OF, max_age_hours=value)
        with self.assertRaises(HandoffError):
            validate_handoff(self.doc(), max_age_hours=24)  # requires as_of


class CLIContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'handoff.json'

    def run_cli(self, data, *args, raw=None):
        self.path.write_text(raw if raw is not None else json.dumps(data))
        return subprocess.run([sys.executable, '-m', 'task_handoff_check', str(self.path), *args],
                              cwd=ROOT, text=True, capture_output=True, timeout=10)

    def test_exit_codes(self):
        self.assertEqual(self.run_cli(handoff()).returncode, 0)
        self.assertEqual(self.run_cli(None, raw='{"x": 1e999}').returncode, 1)
        for bad in ('1e400', 'nan', 'inf', '-1'):
            with self.subTest(option=bad):
                p = self.run_cli(handoff(), '--as-of', AS_OF, '--max-age-hours', bad)
                self.assertEqual(p.returncode, 2)
                self.assertNotIn('Traceback', p.stderr)
        self.assertEqual(self.run_cli(handoff(), '--max-age-hours', '24').returncode, 2)  # missing --as-of

    def test_rh01_cli_long_interval_one_microsecond_over_is_stale(self):
        data = handoff()
        data['updated_at'] = '1000-01-01T00:00:00Z'
        p = self.run_cli(data, '--as-of', '2026-01-01T00:00:00.000001Z', '--max-age-hours', '8993736')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_fabricated_but_consistent_handoff_passes_with_the_scope_disclaimer(self):
        data = handoff()
        data.update(status='complete', next_action=None, owner='Fictional Person',
                    evidence=[{'label': 'review', 'path': 'never-created/review.txt'}],
                    completion_criteria=[{'description': 'Everything done', 'met': True, 'evidence': ['review']}])
        p = self.run_cli(data)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn('This checks assertions, not truth, authorization, file contents, or task completion.', p.stdout)
        report = json.loads(self.run_cli(data, '--json').stdout)
        self.assertEqual((report['ok'], report['scope']), (True, 'handoff_consistency'))

    def test_incomplete_and_contradictory_handoffs_fail(self):
        cases = {
            'missing owner': lambda d: d.pop('owner'),
            'complete with blocker': lambda d: d.update(status='complete', next_action=None,
                                                        blockers=[{'description': 'x waits', 'owner': 'y'}]),
            'complete with unmet criterion': lambda d: d.update(status='complete', next_action=None),
            'met criterion without evidence': lambda d: d['completion_criteria'][0].update(met=True),
            'unknown evidence label': lambda d: d['completion_criteria'][0].update(evidence=['nope']),
            'placeholder owner': lambda d: d.update(owner='TBD'),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                data = handoff()
                mutate(data)
                self.assertEqual(self.run_cli(data).returncode, 1)


class DocumentedCoverage(unittest.TestCase):
    def test_rh02_readme_states_no_version_other_than_the_package_version(self):
        from task_handoff_check import __version__
        readme = (ROOT / 'README.md').read_text()
        for version in re.findall(r'\bVersion (\d+\.\d+\.\d+\S*?)[.\s]', readme):
            self.assertEqual(version, __version__)

    OS_NAMES = {'ubuntu-latest': 'Linux', 'macos-latest': 'macOS', 'windows-latest': 'Windows'}

    def test_thc1_readme_and_ci_matrix_agree_both_ways(self):
        workflow = (ROOT / '.github/workflows/tests.yml').read_text()
        readme = ' '.join((ROOT / 'README.md').read_text().split())
        configured = {(self.OS_NAMES[os_], v) for os_, v in re.findall(r"- os: (\S+)\s+python: '([0-9.]+)'", workflow)}
        self.assertGreaterEqual(len(configured), 2)
        claim = re.search(r'pull-request workflow is configured for (.+?(?:Linux|macOS|Windows))[.;]', readme).group(1)
        claimed = set()
        for part in claim.split(', plus '):
            platform = re.search(r' on (\w+)$', part).group(1)
            claimed |= {(platform, v) for v in re.findall(r'\d+\.\d+', part)}
        self.assertEqual(claimed, configured)  # every claimed job exists, and every configured job is claimed
        self.assertIn('not executed by this change', readme)



class WrongPathTypes(unittest.TestCase):
    # r2 audit observation: load_handoff(None) raised TypeError instead of the documented HandoffError.
    def test_a_path_that_is_not_text_or_path_like_is_a_handoff_error(self):
        class BytesPath:
            def __fspath__(self):
                return b'handoff.json'
        for bad in (None, b'handoff.json', 3, object(), BytesPath()):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(HandoffError):
                    load_handoff(bad)

    # kofi r3 review: an empty path or a directory named a symlink, and an unencodable path named a JSON value.
    def test_a_directory_or_unencodable_path_gets_an_accurate_message(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ('', d):
                with self.subTest(bad=bad), self.assertRaisesRegex(HandoffError, 'regular file .*directory'):
                    load_handoff(bad)
        with self.assertRaisesRegex(HandoffError, 'path is invalid'):
            load_handoff('handoff-\ud800.json')  # not encodable even with surrogateescape


if __name__ == '__main__':
    unittest.main()
