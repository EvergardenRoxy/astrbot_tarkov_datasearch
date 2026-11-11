# Engineering handoff

Current development entry: [development guide](docs/development.md).
Active tasks, known issues and validation limits: [checklist](checklist.md).
Version-facing changes: [CHANGELOG](CHANGELOG.md).

## Historical records

The full record through the 2026-11-11 review inventory is preserved in
[engineering history](docs/archive/engineering-history.md). It includes the
previous consolidation and append-only rules, dated decisions, evidence and
test results; its old release targets and defaults are historical, not current.
The maintainer authorised this documentation consolidation on 2026-11-11.
Six old plans/reviews are consolidated in [decision history](docs/archive/development-history.md),
with immutable links to each original. Pending work lives only in checklist.

## 2026-11-11 — Documentation consolidation

- Shortened the development entry and active checklist; preserved K01–K03 and
  O01/O02 verbatim. Low-probability findings remain record-only, with no fixes.
- Removed completed or superseded tasks; retained unresolved input/cache policy,
  per-session quota semantics, Arena weekly plans, output compatibility,
  optimisation notes and live acceptance limits.
- Archived the complete previous handoff, updating relative links only and
  adding a historical-status banner. Merged six planning/review files into one
  dated decision summary; all removed originals are recoverable at de6e2a6.
- Updated navigation and historical citations. Runtime logic, configuration,
  dependencies, tests, assets and versions are unchanged; two source references
  to retired documentation paths are updated without changing behaviour.

Validation: local Markdown targets/anchors, immutable source paths, known-issue
and archive preservation, Python executable AST equivalence, and git diff --check.
No live API/chat calls, deployment, commit, push or release is part of this work.

Append concise evidence for subsequent work here; link to the owning document
instead of duplicating its full plan or task status.
