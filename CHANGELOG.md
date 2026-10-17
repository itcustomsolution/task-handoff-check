# Changelog

## Unreleased

- Reject exponent-overflow JSON numbers, including positive and negative `1e999`, with `HandoffError`.
- Freshness options no longer raise an undocumented `OverflowError`: integers of any size are compared exactly (never converted to float), and every invalid option raises `HandoffError`.
- Report JSON parse locations without repeating input contents or filenames.
- Restore the documented pull-request Python/platform matrix; distinguish configured jobs from executed verification.
- Correct the installation note; it no longer describes the first release as future.
- PyPI metadata (keywords, classifiers, homepage and changelog URLs), README pitch, badge placeholders and install-from-PyPI note.

## 0.1.0

Initial release of task-handoff-check: dependency-free Python CLI and library, strict JSON
validation, synthetic examples, human and JSON reports, and behavior tests.
