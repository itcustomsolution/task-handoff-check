# Changelog

## Unreleased

- Reject exponent-overflow JSON numbers, including positive and negative `1e999`, with `HandoffError`.
- Bound freshness options to 0..87,840,000 hours without converting arbitrary-size integers to float; invalid options consistently raise `HandoffError`.
- Report JSON parse locations without repeating input contents or filenames.
- Restore the documented pull-request Python/platform matrix; distinguish configured jobs from executed verification.
- Correct the published-release installation note; these changes remain local and unreleased.
- PyPI metadata (keywords, classifiers, homepage and changelog URLs), README pitch, badge placeholders and install-from-PyPI note.

## 0.1.0

Initial release of task-handoff-check: dependency-free Python CLI and library, strict JSON
validation, synthetic examples, human and JSON reports, and behavior tests.
