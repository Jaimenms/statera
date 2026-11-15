# 09 — Internal products, cuts and references over time

Several dissemination products originate from the **same source**: for example, a monthly
employment-flows release, an annual employer census and its monthly version may all read the same
feed of payroll events. Without a place for what is shared, each product would redo the
extraction and the consolidation, with rules that slowly diverge. And the source is **mutable**: the
same reference month changes as declarations arrive, so "the May data" only makes sense with a
cut-off date.

This specification defines three things: **internal** products for what is shared, immutable **cuts**
to link products over time, and the **temporality** of reference tables. It replaces the `domain`
unit type of the first versions.

## Alignment and visibility

Every product declares in `product.odps.yaml` (`customProperties`), in Data Mesh terminology:

| `alignment` | What it is | `visibility` | Output layer |
|---|---|---|---|
| `source` | Extraction from a source system (*source-aligned*) | `internal` | bronze (phase 4) |
| `aggregate` | Canonical entity read by several products (*aggregate*) | `internal` | silver (phase 5) |
| `consumer` (default) | Dissemination product (*consumer-aligned*) | `public` | gold (phase 5) |

A consumer-aligned product **cannot** be internal. An internal product **is not disseminated**: it
has no series, calendar or publication gate; it has authorized consumers and cuts.

ODPS **input ports** (`inputPorts`, per contract) link the products: a product depends on the
internal products that own the contracts it reads. What applies to all consumers (deduplication
across systems, exclusions, deadlines, classifications) **should** live in the aggregate; the scope
of each product (for example, only employees with a formal contract) stays in the consumer.

## Authorized consumers

An internal product **must** declare who may read it and for what purpose (purpose limitation, e.g. LGPD art. 6, I, in Brazil, or
GDPR art. 5(1)(b) in the EU):

```yaml
customProperties:
  - property: authorizedConsumers
    value:
      - {product: employment-flows, purpose: "monthly statistics of formal employment flows"}
      - {query: org.example.labor-stats.query.hires-by-sector-and-municipality, purpose: "on-request service, aggregated"}
      - {role: research-team, purpose: "study on labor turnover", until: "2027-11-30", act: "Decision No. 1/2026"}
```

A **team** that does not own the product may be authorized to read it directly, with `role` (from
`governance/roles.yaml`), `purpose`, `until` (expiry date) and `act` (who authorized it). Reading
happens in the organization's environment, without download; whatever comes out of it goes through
disclosure control (in terms of the *Five Safes*: safe people, projects, settings, data and outputs).
Validation **must** fail when one of these fields is missing, when the role does not exist and when
the deadline has passed: the authorization is renewed by a new act or withdrawn. The library, opened
with the role (`catalog.open(role=…)` or `STATERA_ROLE`), denies reading without a valid
authorization. In engines with roles (PostgreSQL, Trino…), the framework generates the read `grants`
on the output ports from the valid authorizations; that is the real boundary.

Validation **must** fail when a product (through its input ports) or a recurring query (through its
`bases`) reads an internal product without being on the list. In dbt, the framework generates one
`group` per product and the `access` of each model: the output ports and the staging of an internal
product are `public`; its other models, `private`; the models of consumer-aligned products,
`protected`.

## Batches

A source-aligned product extracts **incrementally**. Each run writes an immutable **batch** with what
was received since the last load up to the cut-off date, in
`4-collect/data/bronze/<table>/batch=NNNNNN/part-0.parquet`, with `_metadata.json` (receipt window,
rows, SHA-256, time). Each record is stored once. The bronze contract declares:

| Property | Value |
|---|---|
| `extraction` | `{source, receivedAtColumn, sourceSystem}`: where to extract from and which column dates the receipt |
| `batch` column | Batch number, written by the framework |
| `slaProperties` `retention` | How long batches are kept |

Staging models filter the cut with the framework macro
`{{ statera.at_cut('<received-at column>', '<table>') }}`: received up to the cut-off date **and**
present up to the cut's maximum batch.

## Cuts

A **cut** is what an internal product releases for a cut-off date. It is an **immutable** manifest in
`7-disseminate/data/<cut-off date>/cut.json`, written by the task `py:statera.disseminate.mark_cut`
after the contracts have passed on the cut:

| Field | Content |
|---|---|
| `product`, `urn`, `alignment`, `cutDate` | What was released |
| `run`, `markedAt`, `codeVersion` | Who computed it, when and with what code |
| `authorizedConsumers` | For whom |
| `sources` (source-aligned product) | Per table: contract, source system, maximum batch, rows in the cut, hash of each batch used and hash of the set |
| `outputs` (aggregate) | Per output port: contract, table, rows in the cut's partition |
| `references` (aggregate) | Contract version, temporality, authority and hash of each reference table used |
| `inputs` | The cut manifests of the input internal products |

Rules:

- A cut **must not** be rewritten. Recomputing requires `--force` and is recorded in the run.
- An aggregate's outputs **must** be stored per cut (`customProperties.partitionedBy:
  cut_date` in the contract, with the cut-off date column from `[conventions] cut_date_column`; in
  dbt, incremental materialization with `unique_key = 'cut_date'`), so that recomputing one cut
  does not erase the others. Uniqueness tests apply within the cut (`statera.unique_at_cut`).
- A consumer **waits** for the cut on an intermediate event of the process
  (`statera.awaits: py:statera.collect.pin_cut`, 05) and **pins** it in the edition: it reads only
  that cut's partition, and the batches up to the manifest's batch.
- Two consumers asking for the same date read the **same** cut. A cut already released is reused,
  without new extraction or recomputation.
- A cut blocked in validation is not released: consumers keep waiting.

In the demo, `statera run --with-dependencies` first releases, in order, the missing cuts; in
production, that is the orchestrator's job, and releasing the cut is the event that triggers the
consumers. The equivalent storage in production is Apache Iceberg tables with one *tag* per cut.

## Provenance

Each edition's Data Package (04) records in `x-statera`:

- `cuts`: internal product, cut-off date and run of each cut read;
- `extractions`: per source, the cut-off date, maximum batch, rows and hash, gathered from the manifests;
- `references`: each reference table used, with contract version, temporality, authority and hash.
  Those used inside an internal product come from the cut manifest (field `via`), with the hash from
  the moment the cut was computed.

## References over time

Reference tables change: a new municipality is established, the economic activity classification gets a
new version (e.g. ISIC, or CNAE in Brazil), the occupation classification is updated (ISCO, CBO).
Every reference contract **must** declare `customProperties.temporality`:

| Temporality | Meaning | Requirements | Use |
|---|---|---|---|
| `stable` | The same table applies to the whole period | — | Simple join |
| `validity` | Each row applies in an interval | Validity start and end columns, those of `[conventions]` `valid_from_column` and `valid_to_column` (by default `valid_from` and `valid_to`; empty end = currently valid); intervals of the same key without overlap (generated test) | Join on the event date: `event_date >= valid_from and (valid_to is null or event_date <= valid_to)` |
| `versioned` | The authority publishes versions that do not mix | Version in the contract; `temporalNote` with the policy | The consumer pins the version; across versions, it uses the authority's correspondence table |

`authority` says who maintains the reference table. For long municipal series, stable
geographic units (such as the Minimum Comparable Areas used in Brazil) resolve municipal splits; a series that changes
classification version **should** document the correspondence used in the technical note.

## Conformance

The levels (08) apply to internal products with these requirements in place of the dissemination ones:

| Level | Internal product |
|---|---|
| 1 | Same as the others, plus `authorizedConsumers` |
| 2 | Source-aligned: contract with `extraction` and retention. Aggregate: outputs built by dbt models and stored per cut (`partitionedBy`) |
| 3 | Executable process; contracts with quality rules; aggregate: rules with cases and lineage back to bronze |
| 4 | Service indicators (cut delay, completeness) |
