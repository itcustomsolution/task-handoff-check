import copy
import os
from pathlib import Path
import tempfile
import unittest
from task_handoff_check import HandoffError, load_handoff, validate_handoff
from test_cli import handoff


class LibraryTests(unittest.TestCase):
    def test_validation_does_not_mutate_handoff(self):
        data = handoff()
        original = copy.deepcopy(data)
        self.assertTrue(validate_handoff(data)['ok'])
        self.assertEqual(data, original)

    def test_bad_json_types_return_issues(self):
        values = [None, True, 1, '', [], {}]
        for value in values:
            self.assertFalse(validate_handoff(value)['ok'])
        for field in ('owner', 'status', 'inputs', 'evidence', 'blockers', 'completion_criteria'):
            for value in values:
                with self.subTest(field=field, value=value):
                    data = handoff()
                    data[field] = value
                    result = validate_handoff(data)
                    self.assertIsInstance(result['ok'], bool)

    def test_invalid_time_options_raise_documented_exception(self):
        for options in ({'as_of': 'bad'}, {'max_age_hours': 2},
                        {'as_of': '2026-01-01T00:00:00Z', 'max_age_hours': float('inf')}):
            with self.assertRaises(HandoffError):
                validate_handoff(handoff(), **options)

    def test_rejects_control_text_and_invalid_offset(self):
        data = handoff()
        data['owner'] = 'Sample\x1b[31mOwner'
        self.assertFalse(validate_handoff(data)['ok'])
        data = handoff()
        data['updated_at'] = '2026-01-01T00:00:00+00:60'
        self.assertFalse(validate_handoff(data)['ok'])

    def test_reader_rejects_symlink_directory_fifo_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'bad.json'
            path.write_bytes(b'\xff')
            with self.assertRaises(HandoffError):
                load_handoff(path)
            with self.assertRaises(HandoffError):
                load_handoff(root)
            link = root / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(HandoffError):
                load_handoff(link)
            if hasattr(os, 'mkfifo'):
                pipe = root / 'pipe'
                os.mkfifo(pipe)
                with self.assertRaises(HandoffError):
                    load_handoff(pipe)
