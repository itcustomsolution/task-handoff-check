import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from delivery_evidence_check import EvidenceError, create_manifest, load_manifest, verify_manifest


class LibraryTests(unittest.TestCase):
    def test_create_verify_roundtrip_does_not_mutate_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'notes.txt').write_text('Synthetic notes\n')
            manifest = create_manifest(root, ['notes.txt'])
            original = json.dumps(manifest)
            self.assertTrue(verify_manifest(manifest, root)['ok'])
            self.assertEqual(json.dumps(manifest), original)
            (root / 'notes.txt').write_text('Changed notes\n')
            self.assertFalse(verify_manifest(manifest, root)['ok'])

    def test_bad_json_types_produce_issues_without_reading_files(self):
        candidates = [None, True, 1, '', [], {}]
        base = {'schema_version': 1, 'files': [{'path': 'synthetic.txt', 'sha256': '0' * 64}]}
        for field in ('files', 'local_checks'):
            for value in candidates:
                with self.subTest(field=field, value=value):
                    report = verify_manifest({**base, field: value}, 'not-a-real-root')
                    self.assertFalse(report['ok'])
        for value in candidates:
            report = verify_manifest(value, 'not-a-real-root')
            self.assertFalse(report['ok'])

    def test_claim_schema_rejects_bad_results_duplicate_names_and_empty_proof(self):
        base = {'schema_version': 1, 'files': [{'path': 'synthetic.txt', 'sha256': '0' * 64}]}
        claims = [None, {}, {'name': 'review', 'result': [], 'evidence': []},
                  {'name': 'review', 'result': 'passed', 'evidence': []},
                  {'name': 'review', 'result': 'failed', 'evidence': [None]},
                  {'name': 'review', 'result': 'passed', 'evidence': ['synthetic.txt', 'synthetic.txt']}]
        for claim in claims:
            report = verify_manifest({**base, 'local_checks': [claim]}, 'not-a-real-root')
            self.assertFalse(report['ok'])
            self.assertEqual(report['files_checked'], 0)
        claim = {'name': 'review', 'result': 'not_run', 'evidence': []}
        self.assertFalse(verify_manifest({**base, 'local_checks': [claim, claim]}, '.')['ok'])

    def test_bad_file_json_raises_documented_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'invalid.json'
            path.write_bytes(b'\xff')
            with self.assertRaises(EvidenceError):
                load_manifest(path)

    def test_reader_rejects_all_nonfinite_numbers_including_exponent_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'numbers.json'
            for value in ('1e999', '-1e999', 'NaN', 'Infinity', '-Infinity'):
                with self.subTest(value=value):
                    path.write_text('{"nested":[' + value + ']}')
                    with self.assertRaisesRegex(EvidenceError, 'nonfinite JSON'):
                        load_manifest(path)
            path.write_text('{"finite":[1.5,-2.5e100,1e-999,0.0]}')
            self.assertEqual(load_manifest(path), {'finite': [1.5, -2.5e100, 0.0, 0.0]})

    def test_optional_file_and_total_budgets_fail_closed_at_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'a.txt').write_bytes(b'abc')
            (root / 'b.txt').write_bytes(b'defg')
            manifest = create_manifest(root, ['a.txt', 'b.txt'])
            original = json.dumps(manifest)
            self.assertTrue(verify_manifest(manifest, root, max_file_bytes=4, max_total_bytes=7)['ok'])
            report = verify_manifest(manifest, root, max_file_bytes=2)
            self.assertFalse(report['ok'])
            self.assertEqual(report['files_checked'], 0)
            self.assertEqual({issue['code'] for issue in report['issues']}, {'resource_limit'})
            report = verify_manifest(manifest, root, max_total_bytes=6)
            self.assertFalse(report['ok'])
            self.assertEqual(report['files_checked'], 1)
            self.assertEqual(report['issues'][0]['location'], 'files[1]')
            self.assertEqual(report['issues'][0]['code'], 'resource_limit')
            self.assertEqual(json.dumps(manifest), original)
            self.assertTrue(verify_manifest(manifest, root)['ok'])
            with self.assertRaisesRegex(EvidenceError, 'max_total_bytes'):
                create_manifest(root, ['a.txt', 'b.txt'], max_total_bytes=6)

    def test_zero_byte_budget_allows_empty_files_and_large_integer_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'empty.txt').write_bytes(b'')
            manifest = create_manifest(root, ['empty.txt'], max_file_bytes=0, max_total_bytes=0)
            self.assertTrue(verify_manifest(manifest, root, max_file_bytes=0, max_total_bytes=0)['ok'])
            self.assertTrue(verify_manifest(manifest, root, max_file_bytes=10**400, max_total_bytes=10**400)['ok'])

    def test_invalid_library_budgets_raise_documented_error(self):
        for field in ('max_file_bytes', 'max_total_bytes'):
            for value in (-1, True, 1.5, float('nan'), '10'):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(EvidenceError):
                        create_manifest('.', ['missing.txt'], **{field: value})
                    with self.assertRaises(EvidenceError):
                        verify_manifest({}, '.', **{field: value})

    def test_growing_file_cannot_escape_read_budget(self):
        import delivery_evidence_check.core as core
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'growing.txt'
            path.write_bytes(b'abc')
            manifest = create_manifest(root, ['growing.txt'])
            original_open = core._open_regular
            sizes = []

            @contextmanager
            def grow_on_read(candidate):
                with original_open(candidate) as stream:
                    class GrowingReader:
                        def fileno(self):
                            return stream.fileno()

                        def read(self, size):
                            sizes.append(size)
                            if len(sizes) == 1:
                                with path.open('ab') as writer:
                                    writer.write(b'd')
                            return stream.read(size)
                    yield GrowingReader()

            with patch.object(core, '_open_regular', grow_on_read):
                report = verify_manifest(manifest, root, max_file_bytes=3, max_total_bytes=3)
            self.assertFalse(report['ok'])
            self.assertEqual(report['issues'][0]['code'], 'resource_limit')
            self.assertEqual(report['files_checked'], 0)
            self.assertEqual(sizes, [4])

    def test_create_rejects_empty_paths(self):
        with self.assertRaises(EvidenceError):
            create_manifest('.', [])
