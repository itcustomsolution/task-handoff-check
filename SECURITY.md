# Security and limits

This utility validates one explicitly supplied local JSON handoff. It never
opens referenced evidence paths, fetches URLs, executes commands, changes task
state or dispatches work. A passing result does not establish truth, ownership,
authorization, evidence authenticity, completion, compliance or certification.

Input is capped at 1 MiB. The reader rejects final-component symlinks and
special files. Parent directory symlinks may be followed, and this reader is
not a sandbox against concurrent filesystem changes. Use trusted input paths.
Freshness is evaluated only against an explicit caller-supplied timestamp.

Your own documents may contain private information. Do not publish them as bug
reports. Use a synthetic reproduction. Reports include field locations and
input errors; review them before sharing. URL syntax checks cannot detect every
secret embedded in a path or query, so never put secrets in handoff references.

For suspected security defects, use private vulnerability reporting on this
repository if available. If unavailable, open a minimal issue requesting a
private reporting channel. Do not publish private inputs or exploit details.
