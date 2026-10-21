"""Regressions for DEC-1/DEC-2 and the integrity-versus-truth boundary. Synthetic files only."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import delivery_evidence_check.core as core
from delivery_evidence_check import EvidenceError, create_manifest, load_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[1]
sha = lambda b: hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'report.txt').write_bytes(b'synthetic report')

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'delivery_evidence_check', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=10)


class FabricatedEvidence(Base):
    def test_self_authored_passed_and_failed_claims_both_only_prove_byte_integrity(self):
        (self.root / 'tests-passed.txt').write_bytes(b'ALL TESTS PASSED (fabricated synthetic claim)')
        for result in ('passed', 'failed'):
            with self.subTest(result=result):
                manifest = create_manifest(self.root, ['tests-passed.txt'])
                manifest['local_checks'] = [{'name': 'unit tests', 'result': result, 'evidence': ['tests-passed.txt']}]
                path = self.root / 'm.json'
                path.write_text(json.dumps(manifest))
                p = self.cli('verify', path)
                self.assertEqual(p.returncode, 0)
                self.assertIn('Matching hashes do not prove truth, authenticity, ownership, authorization, test success, or task completion.', p.stdout)
                report = json.loads(self.cli('verify', path, '--json').stdout)
                self.assertEqual((report['ok'], report['scope'], report['claimed_checks']), (True, 'local_file_integrity', 1))

    def test_unsigned_manifest_replacing_file_and_hash_together_passes_as_documented(self):
        manifest = create_manifest(self.root, ['report.txt'])
        (self.root / 'report.txt').write_bytes(b'different bytes')
        self.assertEqual(verify_manifest(manifest, self.root)['issues'][0]['code'], 'hash_mismatch')
        manifest['files'][0]['sha256'] = sha(b'different bytes')
        self.assertTrue(verify_manifest(manifest, self.root)['ok'])  # correspondence to a manifest, not provenance

    def test_mismatched_and_malformed_hashes(self):
        for value, code in ((sha(b'other'), 'hash_mismatch'), ('A' * 64, 'invalid_hash'), ('0' * 63, 'invalid_hash'), (None, 'invalid_hash')):
            with self.subTest(value=value):
                report = verify_manifest({'schema_version': 1, 'files': [{'path': 'report.txt', 'sha256': value}]}, self.root)
                self.assertIn(code, [i['code'] for i in report['issues']])
                self.assertFalse(report['ok'])


class PathsAndFilesystem(Base):
    def test_path_escapes_are_rejected_before_any_read(self):
        for path in ('../report.txt', '/etc/passwd', 'a/../report.txt', './report.txt', 'C:/x', 'a\\b', 'x\x00y', 'x\ny', 'CON', 'trailing.'):
            with self.subTest(path=path):
                report = verify_manifest({'schema_version': 1, 'files': [{'path': path, 'sha256': '0' * 64}]}, self.root)
                self.assertEqual(report['files_checked'], 0)
                self.assertFalse(report['ok'])

    def test_symlinked_file_ancestor_and_root_are_refused(self):
        outside = Path(tempfile.mkdtemp(dir=self.root.parent)).resolve()
        self.addCleanup(lambda: (outside / 'secret.txt').unlink(missing_ok=True) or outside.rmdir())
        (outside / 'secret.txt').write_bytes(b'outside')
        try:
            (self.root / 'link.txt').symlink_to(outside / 'secret.txt')
            (self.root / 'linkdir').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        for rel in ('link.txt', 'linkdir/secret.txt'):
            with self.subTest(rel=rel):
                report = verify_manifest({'schema_version': 1, 'files': [{'path': rel, 'sha256': sha(b'outside')}]}, self.root)
                self.assertEqual([i['code'] for i in report['issues']], ['file_unavailable'])
        report = verify_manifest({'schema_version': 1, 'files': [{'path': 'secret.txt', 'sha256': sha(b'outside')}]}, self.root / 'linkdir')
        self.assertFalse(report['ok'])

    @contextmanager
    def _hook_read(self, on_first_read):
        original = core._open_regular

        @contextmanager
        def hooked(candidate):
            with original(candidate) as stream:
                class Reader:
                    calls = 0
                    def fileno(self):
                        return stream.fileno()
                    def read(self, size):
                        Reader.calls += 1
                        if Reader.calls == 1:
                            on_first_read()
                        return stream.read(size)
                yield Reader()
        with patch.object(core, '_open_regular', hooked):
            yield

    def test_file_replaced_after_open_reports_the_opened_bytes_or_fails_never_a_mix(self):
        original_bytes = b'synthetic report'
        def replace():
            (self.root / 'new.txt').write_bytes(b'replacement bytes')
            os.replace(self.root / 'new.txt', self.root / 'report.txt')
        manifest = {'schema_version': 1, 'files': [{'path': 'report.txt', 'sha256': sha(original_bytes)}]}
        with self._hook_read(replace):
            report = verify_manifest(manifest, self.root)
        # The descriptor still names the opened inode: the comparison is about those bytes only.
        self.assertTrue(report['ok'] or report['issues'][0]['code'] == 'file_unavailable')
        self.assertFalse(verify_manifest(manifest, self.root)['ok'])  # a later run sees the replacement

    def test_growth_during_read_without_limits_is_rejected(self):
        manifest = {'schema_version': 1, 'files': [{'path': 'report.txt', 'sha256': sha(b'synthetic report')}]}
        def grow():
            with (self.root / 'report.txt').open('ab') as w:
                w.write(b' appended')
        with self._hook_read(grow):
            report = verify_manifest(manifest, self.root)
        self.assertFalse(report['ok'])
        self.assertIn(report['issues'][0]['code'], ('file_unavailable', 'hash_mismatch'))

    def test_dec_r1_budget_bounds_bytes_read_from_the_descriptor(self):
        (self.root / 'tiny.bin').write_bytes(b'abc')
        manifest = create_manifest(self.root, ['tiny.bin'])
        offsets = []
        original = core._open_regular

        @contextmanager
        def hooked(candidate):
            with original(candidate) as stream:
                class Reader:
                    def fileno(self):
                        return stream.fileno()
                    def read(self, size):
                        if not offsets:
                            with (self.root_path).open('ab') as w:
                                w.write(b'x' * 4096)  # grows after the size preflight
                        data = stream.read(size)
                        offsets.append(os.lseek(stream.fileno(), 0, os.SEEK_CUR))
                        return data
                r = Reader()
                r.root_path = self.root / 'tiny.bin'
                yield r
        with patch.object(core, '_open_regular', hooked):
            report = verify_manifest(manifest, self.root, max_file_bytes=3, max_total_bytes=3)
        self.assertEqual([i['code'] for i in report['issues']], ['resource_limit'])
        self.assertLessEqual(max(offsets), 4)  # the budget plus the one detection byte, never a buffer's worth

    def test_total_budget_is_cumulative_and_stops_reading_once_spent(self):
        for name in ('a', 'b', 'c'):
            (self.root / name).write_bytes(b'12345')
        manifest = create_manifest(self.root, ['a', 'b', 'c'])
        report = verify_manifest(manifest, self.root, max_total_bytes=10)
        self.assertEqual(report['files_checked'], 2)
        self.assertEqual([(i['location'], i['code']) for i in report['issues']], [('files[2]', 'resource_limit')])
        reads = []
        original = core._digest
        with patch.object(core, '_digest', lambda root, rel, budget=None: reads.append(rel) or original(root, rel, budget)):
            verify_manifest(manifest, self.root, max_total_bytes=4)
        self.assertEqual(reads, ['a', 'b', 'c'])  # each is attempted; none is read past its fstat preflight


class Reader(Base):
    def test_dec2_exponent_overflow_rejected_and_message_has_no_content(self):
        path = self.root / 'SENTINEL_NAME.json'
        for raw in ('{"x": 1e999}', '{"x": -1e999}', '{"x": [1, {"y": 9E+9999}]}'):
            path.write_text(raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(EvidenceError, 'nonfinite'):
                load_manifest(path)
        path.write_text('{"x": "SYNTHETIC_SECRET_SENTINEL" !}')
        with self.assertRaises(EvidenceError) as caught:
            load_manifest(path)
        self.assertNotIn('SENTINEL', str(caught.exception))
        self.assertRegex(str(caught.exception), r'line 1, column \d+')
        for raw, pattern in ((b'{"x": "\xff"}', 'UTF-8'), (b'{"x": ' + b'9' * 5000 + b'}', 'unsupported')):
            path.write_bytes(raw)
            with self.assertRaisesRegex(EvidenceError, pattern):
                load_manifest(path)
        # Deep nesting never escapes as RecursionError. Some interpreters (CPython 3.14.6 here) parse it
        # without a recursion limit; verification then refuses it as an invalid manifest.
        path.write_bytes(b'[' * 100000 + b']' * 100000)
        try:
            data = load_manifest(path)
        except EvidenceError as exc:
            self.assertRegex(str(exc), 'too deep|unsupported|invalid')
        else:
            self.assertFalse(verify_manifest(data, self.root)['ok'])
        path.write_text('{"x": 1e999}')
        with self.assertRaises(EvidenceError) as caught:
            load_manifest(path)
        self.assertNotIn('UTF-8', str(caught.exception))  # no misleading prefix on a nonfinite-number error

    def test_dec_r2_nul_in_root_or_manifest_path_is_an_evidence_error(self):
        manifest = create_manifest(self.root, ['report.txt'])
        report = verify_manifest(manifest, str(self.root) + '\x00x')
        self.assertEqual([i['code'] for i in report['issues']], ['file_unavailable'])
        with self.assertRaisesRegex(EvidenceError, 'NUL'):
            load_manifest(str(self.root / 'm.json') + '\x00')

    def test_wrong_path_types_are_evidence_errors(self):
        manifest = create_manifest(self.root, ['report.txt'])
        for call in (lambda: verify_manifest(manifest, None), lambda: load_manifest(None), lambda: create_manifest(None, ['report.txt'])):
            with self.assertRaisesRegex(EvidenceError, 'path-like'):
                call()

    def test_dec_r2_readme_states_no_version_other_than_the_package_version(self):
        from delivery_evidence_check import __version__
        readme = (ROOT / 'README.md').read_text()
        for version in re.findall(r'\bVersion (\d+\.\d+\.\d+\S*?)[.\s]', readme):
            self.assertEqual(version, __version__)
        self.assertNotIn('After the first PyPI release', readme)

    def test_cli_rejects_bad_limits_with_usage_exit(self):
        manifest = create_manifest(self.root, ['report.txt'])
        path = self.root / 'm.json'
        path.write_text(json.dumps(manifest))
        for value in ('-1', '1.5', 'nan', '9' * 5000, ''):
            with self.subTest(value=value[:10]):
                p = self.cli('verify', path, '--max-file-bytes', value)
                self.assertEqual(p.returncode, 2)
                self.assertNotIn('Traceback', p.stderr)


class DocumentedCoverage(unittest.TestCase):
    OS_NAMES = {'ubuntu-latest': 'Linux', 'macos-latest': 'macOS', 'windows-latest': 'Windows'}

    def test_dec1_readme_and_ci_matrix_agree_both_ways(self):
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

if __name__ == '__main__':
    unittest.main()
