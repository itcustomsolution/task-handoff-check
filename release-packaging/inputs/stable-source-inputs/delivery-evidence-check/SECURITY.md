# Security and limits

This utility reads an explicitly provided JSON manifest and explicitly named
local files. It does not execute checks, fetch URLs, scan directories or write
files. Reports may reveal manifest structure and file errors; treat them as
potentially sensitive when your own inputs are sensitive.

Use trusted, stable local directories. POSIX descriptor-relative no-follow
opens reduce symlink races; fallback platforms have weaker race protection.
This is not a filesystem sandbox and does not promise an atomic snapshot.
Manifest size is capped at 1 MiB. Named evidence files have no size limit by
default; optional `max_file_bytes` and `max_total_bytes` library parameters (or
matching CLI flags) bound hashing reads. They do not impose a wall-clock timeout
or provide filesystem isolation. Up to one extra byte may be read to detect
growth beyond a limit.

A matching SHA-256 confirms bytes against the supplied, unsigned manifest. It
cannot establish truth, authenticity, ownership, provenance, test success,
completion, authorization, compliance or certification. Protect the manifest
through your own trusted distribution or signing process when required.

For a suspected security defect, use this repository's private vulnerability
reporting feature if available. If unavailable, open a minimal issue requesting
a private reporting channel; do not post sensitive evidence or exploit details.
Ordinary validation bugs can be reported with synthetic inputs.
