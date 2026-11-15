# Register of published editions — Foo Bar (minimal example)

This repository is written **by the production process** (publication task of the product's process,
run by `statera`), by automatic commit, for each published edition. Do not edit it by hand. The
code, the contracts and the design of the series live in the content repository (`content`);
only the history of editions lives here:

| File | Content |
|---|---|
| `<series>/<period>.datapackage.json` | Data Package of the edition: files, hashes, cut-off date |
| `<series>/editions.csv` | Series index: one row per edition (and per correction) |
| `<series>/evaluations/<period>.json` and `.md` | Quality indicators of the edition (GSBPM 8) |
| `<series>/README.md` | Generated page: editions, revisions, lineage per version, duration |

The published data files live in the publication storage (open data portal); this repository
holds the metadata and the hashes that prove what was published.

> Demonstration with SYNTHETIC DATA.
