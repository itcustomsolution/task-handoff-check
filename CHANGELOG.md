# Changelog

## Unreleased (0.1.1rc2 candidate, 2026-10-17; not published; 0.1.1rc1 of 2026-10-16 was the independently audited candidate)

- Reject exponent-overflow JSON numbers, including positive and negative `1e999`, with `HandoffError`.
- Freshness options no longer raise an undocumented `OverflowError`: integers of any size are compared exactly (never converted to float), and every invalid option raises `HandoffError`.
- Report JSON parse locations without repeating input contents or filenames.
- Restore the documented pull-request Python/platform matrix; distinguish configured jobs from executed verification.
- Correct the installation note; it no longer describes the first release as future.
- PyPI metadata (keywords, classifiers, homepage and changelog URLs), README pitch, badge placeholders and install-from-PyPI note.
- A handoff path containing a NUL character raises `HandoffError('handoff path is invalid ...')` instead of a misleading JSON message.
- Freshness compares the elapsed time in exact integer microseconds; a long interval just over the limit is now `stale` (independent re-audit RH-01).
- The README no longer states a version number (it said 0.1.0 while the package is 0.1.1rc1; RH-02).
- Local verification of the 0.1.1rc1 candidate (2026-10-16): CPython 3.10, 3.11, 3.12 and 3.14 on macOS (arm64), from source and from the built wheel. Python 3.13, Linux and Windows were not run; no hosted CI run exists for it.

## 0.1.0

Initial release of task-handoff-check: dependency-free Python CLI and library, strict JSON
validation, synthetic examples, human and JSON reports, and behavior tests.
