# Changelog

## 0.2.0 (release preparation; NOT PUBLISHED)

- Reject exponent-overflow JSON numbers such as `1e999` and `-1e999` with `EvidenceError`, matching the documented finite-number rule.
- New optional byte budgets: `max_file_bytes` / `max_total_bytes` (library, keyword-only) and `--max-file-bytes` / `--max-total-bytes` (CLI, `create` and `verify`). Oversized files are refused before reading, growth past a limit is detected with at most one extra byte read, and the total budget counts every byte read in one call. Exceeding a budget is the new `resource_limit` issue (exit 1). Defaults are unchanged: no limit.
- Human output now also says matching hashes do not prove authenticity or authorization.
- Restore the documented pull-request Python/platform matrix; README distinguishes configured jobs from executed verification.
- PyPI metadata (keywords, classifiers, homepage and changelog URLs), README pitch, badge placeholders and install-from-PyPI note.
- A root or manifest path containing a NUL character is an `EvidenceError` (a `file_unavailable` issue during verification) instead of a raw `ValueError`.
- Files are read unbuffered, so a byte budget bounds the bytes actually read from the file (at most one extra byte to detect growth); a buffered reader could prefetch thousands of bytes from a growing file (independent re-audit DEC-R1). The manifest is read in a loop up to its 1 MiB limit.
- A root or manifest path that is not a string or path-like object raises `EvidenceError` instead of `TypeError`.
- README: no version number (it said 0.1.0 for the 0.2.0rc1 candidate) and the published release is described as existing, not future (DEC-R2).
- Local verification of the 0.2.0rc1 candidate (2026-10-16): CPython 3.10, 3.11, 3.12 and 3.14 on macOS (arm64), from source and from the built wheel. Python 3.13, Linux, Windows and the non-descriptor fallback path were not run; no hosted CI run exists for it.

## 0.1.0

Initial release of delivery-evidence-check: dependency-free Python CLI and library, strict JSON
validation, synthetic examples, human and JSON reports, and behavior tests.
