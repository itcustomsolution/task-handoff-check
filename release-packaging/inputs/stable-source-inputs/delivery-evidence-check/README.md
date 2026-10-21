# delivery-evidence-check

> Fingerprint delivered files with SHA-256 so you can check later that nothing was swapped or edited, provided the manifest itself is kept somewhere the other party cannot change.

<!-- Badges: enable after first PyPI publish -->
<!-- [![PyPI](https://img.shields.io/pypi/v/delivery-evidence-check)](https://pypi.org/project/delivery-evidence-check/) -->
<!-- [![tests](https://github.com/itcustomsolution/delivery-evidence-check/actions/workflows/tests.yml/badge.svg)](https://github.com/itcustomsolution/delivery-evidence-check/actions/workflows/tests.yml) -->
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Check that the local files accompanying a handoff still match an explicit
SHA-256 manifest. Create a manifest from named files, verify it later, and get
clear errors for changed, missing or unsafe paths.

**A matching hash proves byte integrity relative to the manifest. It does not
prove that a report is true, that a test passed, or that work is complete.**

A small open-source engineering utility from [IT Custom Solution](https://github.com/itcustomsolution).
Python 3.10+, no runtime dependencies, no network requests, no commands executed.
MIT licensed.

## Try it in one minute

```sh
git clone https://github.com/itcustomsolution/delivery-evidence-check.git
cd delivery-evidence-check
python -m delivery_evidence_check verify examples/manifest.json
python -m delivery_evidence_check verify examples/manifest.json --json
```

The first command reports `PASS: local file integrity; 1 file(s) read.` The
example and its claimed review are fictional. Change a byte in
`examples/artifacts/review.txt` and the next verification reports `hash_mismatch`
with exit code 1.

Create a manifest for explicit files; paths are relative to `--root`:

```sh
python -m delivery_evidence_check create --root examples artifacts/review.txt > manifest.json
python -m delivery_evidence_check verify manifest.json --root examples
```

`create` prints JSON to stdout. The shell redirection writes the output file;
the tool itself never writes, scans directories, expands globs or runs a check.
List each file explicitly. Do not redirect output onto a listed input file.
Verification defaults to the manifest's directory unless `--root` is supplied.

Install the latest published release with `pipx install delivery-evidence-check` (or
`pip install delivery-evidence-check`). This README describes the source tree, which may contain changes
that are not released yet.

To install from source in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
delivery-evidence-check --version
```

Installation uses setuptools as a build dependency. Running from the checkout
needs only the Python standard library; no package registry release is required.

## Manifest format

```json
{
  "schema_version": 1,
  "files": [
    {
      "path": "artifacts/review.txt",
      "sha256": "41c8044b49434ee47032e107981ba6898e33ca801b77ecb143d6661382548903"
    }
  ],
  "local_checks": [
    {
      "name": "Synthetic greeting review",
      "result": "passed",
      "evidence": [
        "artifacts/review.txt"
      ]
    }
  ]
}
```

- `schema_version`: integer `1`.
- `files`: nonempty array of objects containing only `path` and `sha256`.
- `path`: a relative, forward-slash-separated portable path. Empty components,
  `.`/`..`, absolute paths, backslashes, reserved device names, control characters,
  Windows-reserved punctuation and trailing dots/spaces are rejected.
- `sha256`: 64 lowercase hexadecimal characters. Duplicate paths and paths
  colliding after Unicode NFC normalization and case folding are rejected.
- `local_checks`: optional array. Each check has a nonempty unique `name`,
  `result` (`passed`, `failed`, or `not_run`) and an `evidence` array of exact
  file paths already listed in `files`. A claimed pass/failure needs evidence.
  Repeated evidence paths are rejected.

Unknown fields, malformed UTF-8/JSON, duplicate JSON keys and nonfinite numbers
are rejected, including exponent overflow such as `1e999`. The manifest limit
is 1 MiB. Files are hashed in chunks. By default, explicitly listed evidence
files have no size limit. Set `--max-file-bytes` and/or `--max-total-bytes` on
`create` or `verify` to bound accepted file sizes and total evidence reads:

```sh
python -m delivery_evidence_check verify examples/manifest.json \
  --max-file-bytes 1048576 --max-total-bytes 10485760 --json
```

Limits are nonnegative integer byte counts; zero permits empty files. A file
that is already too large is refused before its contents are read. The reader
also enforces the limit while reading, with at most one extra byte read to
detect a file growing past a limit. Bytes read from an unsuccessful file count
toward the total budget. Each invocation starts a fresh budget. These are byte
limits, not wall-clock timeouts or filesystem isolation; use trusted stable
files appropriate to your time and disk budget.

`local_checks` records **claims**. Even a claimed `failed` or `not_run` check can
have an integrity-valid manifest. No check result changes the meaning of a
successful integrity comparison. `create` produces only the file list; add
claimed metadata explicitly if it is useful to the recipient.

## Reports and exit codes

JSON reports contain `ok`, `scope` (`local_file_integrity`), `files_checked`,
`claimed_checks` and `issues`. Each issue has `code`, `location` and `message`.
`files_checked` counts files successfully read, including hash mismatches.
Schema errors prevent evidence-file reads. Other file errors are collected.

| Exit | Meaning |
| --- | --- |
| 0 | Manifest and file integrity checks pass; or help/version displayed |
| 1 | Invalid manifest, unreadable/unsafe file, or hash mismatch |
| 2 | Invalid command-line usage |

Common issue codes: `input_error`, `invalid_schema`, `unknown_field`,
`invalid_path`, `duplicate_path`, `invalid_hash`, `unknown_evidence`,
`file_unavailable`, `hash_mismatch`, `resource_limit`. A resource-limit failure
returns exit 1 and never a successful integrity comparison. Treat message wording as human-facing.
`create` errors go to stderr and produce no JSON; `verify --json` validation
errors are JSON on stdout. CLI usage errors are on stderr.

## Library

```python
from delivery_evidence_check import create_manifest, load_manifest, verify_manifest

manifest = create_manifest("examples", ["artifacts/review.txt"])
report = verify_manifest(manifest, "examples")
assert report["ok"]

loaded = load_manifest("examples/manifest.json")
report = verify_manifest(loaded, "examples")
```

`verify_manifest` returns a report for invalid documents. `load_manifest` and
`create_manifest` raise `EvidenceError` for input errors. `create_manifest` and
`verify_manifest` accept optional keyword-only `max_file_bytes` and
`max_total_bytes`; invalid limits raise `EvidenceError`. All APIs are read-only.

## Limits and security

Symlinks in file, root and ancestor paths are refused. Directories, FIFOs and
other special files are refused. On systems supporting descriptor-relative
opens and `O_NOFOLLOW`, every component is opened without following symlinks.
Other systems use component checks with weaker race protection. Use stable,
trusted local directories; this is not a sandbox for hostile concurrent writers.
Resolve any trusted operating-system directory aliases before choosing a root.

A file that changes during reading is rejected when metadata changes are
observed, but the tool cannot provide an atomic snapshot of multiple files.
The manifest is unsigned: changing both the file and its recorded hash defeats
this comparison. No authenticity, provenance, ownership, completeness, malware
safety, certification or deployment claim follows from `ok: true`.

Read [SECURITY.md](SECURITY.md) before using sensitive evidence. Never commit
private evidence or credentials just to demonstrate this tool.

## Development

```sh
python -m unittest discover -s tests -v
```

Tests use temporary directories and synthetic data. The pull-request workflow
is configured for Python 3.10, 3.12 and 3.14 on Linux, plus Python 3.14 on macOS.
Configured jobs are not executed by this change; workflow run results are the
evidence of executed coverage. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

Issues and small pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Run `python -m unittest discover -s tests` before opening a PR. Release history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT, see [LICENSE](LICENSE).
