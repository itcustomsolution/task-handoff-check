# task-handoff-check

Catch incomplete or contradictory task handoffs before someone else picks up
the work. This small CLI and library checks an explicit JSON document for a
clear owner, next action, blockers, completion criteria and evidence references.

**A passing handoff is structurally consistent. It is not proof that its claims
are true, that its references exist, or that anyone is authorized to act.**

An open-source engineering utility from [IT Custom Solution](https://github.com/itcustomsolution).
Python 3.10+, no runtime dependencies, no network access, no commands executed.
Version 0.1.0. MIT licensed.

## Try it in one minute

```sh
git clone https://github.com/itcustomsolution/task-handoff-check.git
cd task-handoff-check
python -m task_handoff_check examples/in-progress.json
python -m task_handoff_check examples/complete.json --json
python -m task_handoff_check examples/invalid-complete.json --json
```

The first two checks pass. The third exits 1 because it claims `complete` while
listing an open blocker. All examples, people and references are fictional;
referenced paths deliberately need not exist. The tool never opens them.

To install a command in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
task-handoff-check --version
```

Installation uses setuptools as a build dependency. Running from the checkout
needs only the Python standard library; no package registry release is required.

## Handoff format

```json
{
  "schema_version": 1,
  "objective": "Review the sample release notes",
  "owner": "Sample Maintainer",
  "status": "in_progress",
  "next_action": "Compare the notes with the sample changelog",
  "inputs": [
    {
      "label": "changelog",
      "path": "docs/changelog.md"
    }
  ],
  "evidence": [],
  "blockers": [],
  "completion_criteria": [
    {
      "description": "All sample changes reviewed",
      "met": false,
      "evidence": []
    }
  ],
  "updated_at": "2026-01-01T09:00:00Z"
}
```

All shown fields except `updated_at` are required. Unknown fields are errors.

| Field | Rule |
| --- | --- |
| `schema_version` | Integer `1` |
| `objective`, `owner` | Meaningful nonempty text |
| `status` | `ready`, `in_progress`, `blocked`, or `complete` |
| `next_action` | Meaningful text for unfinished work; JSON `null` for complete |
| `inputs`, `evidence` | Arrays of reference objects; may be empty |
| `blockers` | Array of objects with `description` and `owner`; open blockers only |
| `completion_criteria` | Nonempty array with `description`, boolean `met`, and `evidence` |
| `updated_at` | Optional timestamp with seconds and timezone |

A reference contains a meaningful `label` and exactly one `path` or `url`.
Paths must be portable relative paths without traversal, control characters,
reserved device names or Windows-reserved punctuation. URLs must be absolute
HTTP(S) references without embedded credentials. Labels must be distinct within
each array after Unicode NFC normalization and case folding.

Each criterion's `evidence` is an array of exact labels from the top-level
`evidence` array. A `met: true` claim requires at least one such reference.
Repeated evidence labels and repeated criterion descriptions are rejected.
Reference content and accessibility are never checked.

`blocked` requires at least one open blocker and a concrete next action.
Any open blocker requires `blocked`. `complete` requires all criteria to be
`met: true`, no blockers and a null `next_action`. Other statuses may contain
met criteria while further work remains. This is deliberately a small task
state model; it is not a general workflow engine.

Blank text, control characters and exact placeholders such as `TBD`, `todo`,
`unknown`, `unassigned`, `n/a`, `none`, `-` and `?` are rejected. The tool does
not understand free text semantically: an unhelpful sentence can still pass.

## Optional, reproducible freshness checks

Freshness is never inferred from the machine's current time. Supply `--as-of`
to compare timestamps; optionally specify an inclusive maximum age:

```sh
python -m task_handoff_check examples/in-progress.json \
  --as-of 2026-01-02T09:00:00Z --max-age-hours 24 --json
```

This example passes exactly at the 24-hour boundary. One second later it is
`stale`. `--max-age-hours` must be finite, nonnegative and accompanied by
`--as-of`. Supplying `--as-of` requires a valid `updated_at` and rejects a future
update. `--as-of` alone compares chronology, without a maximum-age policy.

Accepted timestamps use `YYYY-MM-DDTHH:MM:SS`, optional 1–6 fractional digits,
and `Z` or an explicit `+HH:MM`/`-HH:MM` offset. Leap seconds are unsupported.
The timestamp remains an assertion from the document author.

## Reports and exit codes

JSON reports contain `ok`, `scope` (`handoff_consistency`), `freshness`, and an
`issues` array of `code`, `location` and `message`. `freshness` is one of
`not_evaluated`, `unavailable`, `timestamp_compared`, `future`, `within_limit`
or `stale`. Other schema errors can coexist with a valid timestamp comparison.

| Exit | Meaning |
| --- | --- |
| 0 | Consistency checks pass; or help/version displayed |
| 1 | Invalid input, inconsistent handoff or failed timestamp check |
| 2 | Invalid command-line usage or freshness options |

Common issue codes: `invalid_schema`, `unknown_field`, `missing_text`,
`status_conflict`, `invalid_reference`, `duplicate_label`, `duplicate_criterion`,
`incomplete_criterion`, `missing_evidence`, `unknown_evidence`, `invalid_timestamp`,
`missing_timestamp`, `future_timestamp`, `stale`, `input_error`.
Messages are human-facing. `--json` validation reports use stdout; CLI usage
errors use stderr.

## Library

```python
from task_handoff_check import load_handoff, validate_handoff

handoff = load_handoff("examples/complete.json")
report = validate_handoff(handoff)
assert report["ok"]
report = validate_handoff(
    handoff, as_of="2026-01-02T09:00:00Z", max_age_hours=24
)
```

`validate_handoff` returns issues for invalid documents and raises `HandoffError`
for invalid freshness options. `load_handoff` raises `HandoffError` for input
errors. Validation does not mutate the document.

## Limits and development

Input must be a regular UTF-8 JSON file of at most 1 MiB; final-component
symlinks, duplicate JSON keys and nonfinite numbers are rejected. Parent
symlinks may be followed for the explicitly supplied input document. Use
trusted local input locations; the input reader is not a filesystem sandbox.

The tool performs no authentication, authorization, link checking, ownership
verification, work execution, scheduling, agent dispatch or compliance assessment.
It never changes a task's state. See [SECURITY.md](SECURITY.md).

```sh
python -m unittest discover -s tests -v
```

Tests use synthetic inputs and temporary directories. CI covers Python 3.10,
3.12 and 3.14 on Linux, plus Python 3.14 on macOS.
See [CONTRIBUTING.md](CONTRIBUTING.md).
