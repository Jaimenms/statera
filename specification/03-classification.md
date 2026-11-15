# 03 — Classification

Four independent dimensions, to separate **who may receive** from **what the data contains**:

| Dimension | Values | Legal basis (example: Brazil) |
|---|---|---|
| `sharing` | `broad` · `restricted` · `specific` | Decree No. 10,046/2019 (sharing levels) |
| `nature` | `aggregated` · `deidentified` · `personal` · `sensitive-personal` | Law No. 13,709/2018 (LGPD) |
| `secrecy` (confidentiality) | `none` · `statistical` · `tax` · `other` | Specific legislation; Law No. 12,527/2011 (LAI) |
| `disclosureControl` (statistical disclosure control) | absent; `cell-suppression` `{column, minimum, suppressedColumns, unpublishedColumns}` (aggregates); or `k-anonymity` `{k, quasiIdentifiers, suppressibleColumns, unpublishedColumns}` (de-identified microdata) | GSBPM 6.4 |

The dimensions themselves are not tied to any jurisdiction. The last column shows, as an example, the
Brazilian legislation they map to (data-sharing decree, LGPD data protection law, LAI access to
information law); an organization elsewhere maps them to its own (for example, the GDPR for `nature`).

## Where it lives

**In the contract** (ODCS profile, 04):

```yaml
schema:
  - properties:
      - {name: net_change, classification: broad}
      - {name: establishments, classification: restricted}                # not published
      - {name: worker_id, classification: specific, tags: [personal-data]}
customProperties:
  - property: classification
    value:
      sharing: broad
      nature: aggregated
      secrecy: statistical
      disclosureControl: {method: cell-suppression, column: establishments, minimum: 3,
                          suppressedColumns: [hires, separations, net_change], unpublishedColumns: [establishments]}
```

On a column, `classification` is the sharing level; personal data carries the tag
`personal-data` or `sensitive-personal-data`.

**In the approval** (`<unit>/1-specify/classification.yaml`, maintained by the data protection
officer): sharing, nature, confidentiality, control method and legal basis of each asset of the unit
(`assets`, each with `contract`, `sharing`, `nature`, `secrecy`, `disclosure_control` and `legal_basis`;
at the top, `unit`, `approved_by` and `reviewed_on`). CI
**must** require that contract and approval match: whoever writes the contract does not approve their
own classification alone.

## Consistency (checked without data)

- Column with sensitive personal data → table `sensitive-personal`; with personal data → `personal` or higher.
- `broad` table → every column `broad`, except those listed in `unpublishedColumns`.

## Open publication gate

A table only goes to open publication if: sharing is `broad`; nature is `aggregated` or
`deidentified`; confidentiality is `none`, or `statistical` **with** disclosure control declared and
applied; and no published column is non-broad or personal data. `deidentified` microdata only go out
with `k-anonymity`: de-identified is not anonymized (in Brazil, LGPD, art. 12; in the EU, GDPR recital 26), and rare combinations of
quasi-identifiers (municipality, section, sex, ranges) can re-identify a person. The method applies
local suppression, in the order of `suppressibleColumns` (like sdcMicro's `localSuppression`), and
removes whatever is still below k; the package records the erased values and the removed records. The
framework applies the gate when generating the package, in queries with broad output and, statically,
in declared consumers (`exposures` with `meta.sharing: broad`).

Classification is metadata: actual access protection belongs to the engine, with policies generated
from the contracts.
