# Recurring queries

`queries/<theme>/<slug>/`: `query.yaml`, `<slug>.sql` (dbt analysis) and `test.yaml`. Test them with `statera query test`.

Every query lives here, including the ones that serve a single product: its `product` axis says which, and
`governance/shared-owners.yaml` can give its review to that product's team.
