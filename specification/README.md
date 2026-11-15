# Statera specification — version 0.1 (draft)

This specification states **how an organization organizes its statistical products in a repository** so
that any tool implementing it (the `statera` framework is the reference implementation) can validate,
run and document them and federate the catalog. It does not invent formats where a standard already
exists: it uses ODCS, ODPS, Frictionless Data Package, OpenLineage, BPMN 2.0, GSBPM and dbt, and only
defines **profiles** on top of them and what is missing between them.

| Document | Subject |
|---|---|
| [01 — Content repository](01-content-repository.md) | Repository layout (`content/`, `publications/`, `docs/`), `statera.toml`, units, phases, data folders, generated files |
| [02 — Namespace](02-namespace.md) | Unique identifiers across organizations |
| [03 — Classification](03-classification.md) | Sharing, nature, confidentiality and statistical disclosure control; the publication gate |
| [04 — Standards profiles](04-profiles.md) | What the standard requires beyond ODCS, ODPS, Data Package and OpenLineage |
| [05 — Processes and execution](05-processes-and-execution.md) | YAML mirroring BPMN; the to-be process as the execution plan |
| [06 — Controls, indicators and series](06-controls-indicators-series.md) | Declarative formats for phases 6, 7 and 8 |
| [07 — Consumption](07-consumption.md) | Recurring queries, metrics, recipes and library |
| [08 — Conformance levels](08-conformance-levels.md) | Gradual adoption: catalogued, reproducible, controlled, evaluated |
| [09 — Internal products, cuts and references over time](09-internal-products-cuts-and-references.md) | Product alignment and visibility; authorized consumers; batches, cuts and provenance; temporality of reference tables |


The schemas (JSON Schema) are in `src/statera/schemas/statera/` (own) and
`src/statera/schemas/standards/` (official, copied at the adopted version).

## Versioning and change

- **SemVer for the specification.** Breaking changes only in a major version, with a migration window
  and a converter when possible. The content repository declares the version it follows
  (`content/statera.toml`, `[project] specification`).
- **Change process by RFC**, as in the Bitol standards (ODCS, ODPS): public proposal, comment
  period, recorded decision. Decision-making body to be defined.
- **Reference implementation and public conformance tests**: the framework and its CI.
- Normative terms: **must** (mandatory at the stated level), **should** (recommended), **may**.
