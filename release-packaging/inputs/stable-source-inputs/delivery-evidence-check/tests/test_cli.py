"""Behavior checks use only synthetic files in temporary directories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ABC_HASH = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'


class EvidenceCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # macOS's /var can be a symlink; this fixture deliberately chooses a real root.
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'report.txt').write_bytes(b'abc')
        self.manifest = {'schema_version': 1, 'files': [{'path': 'report.txt', 'sha256': ABC_HASH}]}

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'delivery_evidence_check', *map(str, args)],
                              cwd=PACKAGE_ROOT, capture_output=True, text=True, timeout=10)

    def check(self, manifest=None):
        path = self.root / 'evidence.json'
        path.write_text(json.dumps(self.manifest if manifest is None else manifest))
        return self.run_cli('verify', path, '--json')

    def assert_invalid(self, result, code=None):
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertFalse(data['ok'])
        if code:
            self.assertIn(code, [x['code'] for x in data['issues']])
        return data

    def test_verifies_known_hash_without_writing_files(self):
        self.assertEqual(self.check().returncode, 0)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.run_cli('verify', self.root / 'evidence.json', '--json')
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_create_outputs_only_explicit_files_and_known_digest(self):
        (self.root / 'unlisted.txt').write_text('unlisted')
        result = self.run_cli('create', '--root', self.root, 'report.txt')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.manifest)

    def test_changed_and_missing_files_are_distinguished(self):
        (self.root / 'report.txt').write_text('changed')
        self.assert_invalid(self.check(), 'hash_mismatch')
        (self.root / 'report.txt').unlink()
        self.assert_invalid(self.check(), 'file_unavailable')

    def test_empty_file_hash(self):
        (self.root / 'report.txt').write_bytes(b'')
        self.manifest['files'][0]['sha256'] = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        self.assertEqual(self.check().returncode, 0)

    def test_rejects_nonportable_or_traversing_paths(self):
        for path in ('../report.txt', '/report.txt', 'a/../report.txt', './report.txt',
                     'a//b', 'a\\b', 'C:report.txt', 'report.txt/', 'NUL', 'a.', 'a ', 'a\n'):
            with self.subTest(path=path):
                self.manifest['files'][0]['path'] = path
                self.assert_invalid(self.check(), 'invalid_path')

    def test_rejects_duplicate_and_case_colliding_paths(self):
        for path in ('report.txt', 'REPORT.TXT'):
            with self.subTest(path=path):
                self.manifest['files'].append({'path': path, 'sha256': ABC_HASH})
                self.assert_invalid(self.check(), 'duplicate_path')
                self.manifest['files'].pop()

    def test_rejects_symlink_file_and_parent(self):
        try:
            (self.root / 'link.txt').symlink_to(self.root / 'report.txt')
        except OSError:
            self.skipTest('symlinks unavailable')
        self.manifest['files'][0]['path'] = 'link.txt'
        self.assert_invalid(self.check(), 'file_unavailable')
        (self.root / 'linked-dir').symlink_to(self.root, target_is_directory=True)
        self.manifest['files'][0]['path'] = 'linked-dir/report.txt'
        self.assert_invalid(self.check(), 'file_unavailable')

    def test_rejects_symlink_manifest_and_root(self):
        self.check()
        (self.root / 'link.json').symlink_to(self.root / 'evidence.json')
        result = self.run_cli('verify', self.root / 'link.json', '--json')
        self.assert_invalid(result, 'input_error')
        (self.root / 'alias').symlink_to(self.root, target_is_directory=True)
        self.assert_invalid(self.run_cli('verify', self.root / 'evidence.json', '--root',
                                         self.root / 'alias', '--json'), 'file_unavailable')

    def test_rejects_directory_and_fifo_without_hanging(self):
        (self.root / 'folder').mkdir()
        self.manifest['files'][0]['path'] = 'folder'
        self.assert_invalid(self.check(), 'file_unavailable')
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.root / 'pipe')
            self.manifest['files'][0]['path'] = 'pipe'
            self.assert_invalid(self.check(), 'file_unavailable')

    def test_claimed_results_are_metadata_not_executed(self):
        self.manifest['local_checks'] = [{'name': 'synthetic review', 'result': 'failed',
                                         'evidence': ['report.txt']}]
        result = self.check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['claimed_checks'], 1)

    def test_rejects_unknown_check_evidence(self):
        self.manifest['local_checks'] = [{'name': 'example', 'result': 'passed', 'evidence': ['absent.txt']}]
        self.assert_invalid(self.check(), 'unknown_evidence')

    def test_rejects_invalid_schema_types_and_unknown_fields(self):
        for value in (None, [], {}, {'schema_version': True, 'files': []},
                      {'schema_version': 1, 'files': []},
                      {**self.manifest, 'surprise': True},
                      {'schema_version': 1, 'files': [None]}):
            with self.subTest(value=value):
                self.assert_invalid(self.check(value) if value is not None else self.check([]))

    def test_invalid_hash_does_not_trigger_file_read(self):
        self.manifest['files'][0] = {'path': 'not-present', 'sha256': 'wrong'}
        data = self.assert_invalid(self.check(), 'invalid_hash')
        self.assertNotIn('file_unavailable', [x['code'] for x in data['issues']])

    def test_rejects_duplicate_json_keys_and_nonfinite_values(self):
        path = self.root / 'bad.json'
        for contents in ('{"schema_version":1,"schema_version":2,"files":[]}', '{"value":NaN}',
                         '{"value":1e999}', '{"value":-1e999}', '{'):
            path.write_text(contents)
            self.assert_invalid(self.run_cli('verify', path, '--json'), 'input_error')

    def test_optional_budgets_preserve_json_and_create_exit_contracts(self):
        self.check()
        path = self.root / 'evidence.json'
        result = self.run_cli('verify', path, '--max-file-bytes', '3', '--max-total-bytes', '3', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['scope'], 'local_file_integrity')
        for flag in ('--max-file-bytes', '--max-total-bytes'):
            self.assert_invalid(self.run_cli('verify', path, flag, '2', '--json'), 'resource_limit')
            result = self.run_cli('create', '--root', self.root, flag, '2', 'report.txt')
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertIn('max_', result.stderr)
            for value in ('-1', 'nan', '1.5'):
                result = self.run_cli('verify', path, flag, value, '--json')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')

    def test_rejects_oversized_input(self):
        path = self.root / 'large.json'
        path.write_bytes(b' ' * (1024 * 1024 + 1))
        self.assert_invalid(self.run_cli('verify', path, '--json'), 'input_error')

    def test_create_rejects_invalid_files_and_duplicates(self):
        for paths in (('missing',), ('../file',), ('report.txt', 'report.txt')):
            result = self.run_cli('create', '--root', self.root, *paths)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertTrue(result.stderr)

    def test_human_output_names_integrity_scope(self):
        self.check()
        result = self.run_cli('verify', self.root / 'evidence.json')
        self.assertEqual(result.returncode, 0)
        self.assertIn('integrity', result.stdout.lower())

    def test_help_and_version(self):
        self.assertEqual(self.run_cli('--help').returncode, 0)
        # Release-candidate versions differ from 0.1.0: compare with the package metadata, not a literal.
        from delivery_evidence_check import __version__
        self.assertIn(f'version = "{__version__}"', (PACKAGE_ROOT / 'pyproject.toml').read_text())
        self.assertEqual(self.run_cli('--version').stdout.strip(), __version__)


if __name__ == '__main__':
    unittest.main()
