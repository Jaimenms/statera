# 08 — Conformance levels

Not every organization has a data engineering team. The levels allow gradual adoption (in the spirit of RAP
and of the five stars of open data). They are cumulative and checked **without data**.

| Level | Requires | Gives the organization |
|---|---|---|
| **1 — Catalogued** | `product.odps.yaml`, `classification.yaml`, `owners.yaml`; each output port with a contract in the profile (urn, consistent classification) and approved; dissemination product: where the public accesses it (`series.yaml` or declared `distributions`). A contract may declare `manualProduction` instead of the dbt node | Joining the federated catalog; answering "what exists and who is responsible" |
| **2 — Reproducible** | `calendar.yaml` (cut-off dates); `series.yaml`; each output built by a dbt model in the repository; at least one catalogued recurring query | Reproducing any published number |
| **3 — Controlled** | Business rule with cases in the product or in an internal product it depends on; controls; outputs that pass the publication gate; executable to-be process and generated BPMN; lineage of every output back to a bronze source | Stopping errors before publication |
| **4 — Evaluated** | `indicators.yaml` with valid calculations and action plan; `legacyParity` `done` or `not-applicable` | Measurable continuous improvement |

- The product declares its **target** in `product.odps.yaml` (`customProperties`, `conformanceLevel`);
  validation requires the target. Level **0** is the draft: `statera new-product` creates the product
  with it; validation accepts the skeleton and `statera conformance` shows what is missing for level 1.
- `statera validate --level N` checks only what level N requires, for all products.
- `statera conformance` shows the level reached and what is pending for the next one.
- The catalog shows the level reached by each product (`docs/catalog.md`); the federated catalog
  should too.
- Internal products (source-aligned and aggregate) have their own requirements at levels 1 to 4 (09).
