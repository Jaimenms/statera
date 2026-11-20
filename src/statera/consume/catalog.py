"""Library for querying all medallion layers, for those who analyze the data.

    from statera.consume import catalog

    cat = catalog.open()
    cat.search(theme="formal-employment", layer="gold")   # discovery along the call's axes
    print(cat.describe("gold.balance_by_uf_monthly"))     # purpose, columns, limitations, classification

    t = cat.table("gold.balance_by_uf_monthly")           # CURRENT mode: Ibis table (lazy)
    t.filter(t.period >= "2026-01").group_by("region").agg(balance=t.balance.sum()).to_pandas()

    cat.table("bronze.source_events", cut_date="2026-07-20")   # CUT-OFF DATE mode
    ed = cat.edition("employment", "2026-06")                    # PUBLISHED EDITION mode
    ed.table("balance-by-uf"); ed.provenance                     # hash checked

    cat.metric("balance", by=["region"], period="2026-06")
    cat.query("balance-series-by-uf", uf="SP", period_start="2026-01", period_end="2026-06")

Where the data is read from (statera.consume.connection): the shared engine the data team declares in
statera.toml ([consumption] engine) or STATERA_ENGINE, else the local engine built by `statera run`;
`catalog.open(engine="local")` forces the local one. Published editions are read from the local
publication folder or, when it is not here, from the publication storage of the series (`files_url` in
series.yaml), with the hash checked either way.

Ibis translates the same code into the SQL of other engines (PostgreSQL, Trino, Spark): the analysis
written in the notebook with DuckDB runs on the server without rewriting. The contract's classification is
SHOWN and raises a warning, but the real protection belongs to the engine (permissions generated from the
contracts): the library is a convenience, not a security boundary.
"""

from __future__ import annotations

import hashlib
import io
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import ibis
import pandas as pd

from statera import classification, contracts
from statera.consume import connection, metrics, queries
from statera.disseminate import registry
from statera.project import Project, current


class ClassificationWarning(UserWarning):
    """The table read is not for broad sharing: be careful with what leaves the organization."""


class AccessDenied(PermissionError):
    """The given role has no authorization within its validity period to read the internal product."""


class CutNotAvailable(LookupError):
    """The engine does not keep the table at that cut-off date."""


@dataclass
class Description:
    table: str
    urn: str
    unit: str
    layer: str
    purpose: str
    usage: str
    limitations: str
    classification: str
    columns: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"{self.table}  ({self.urn})",
            f"  unit: {self.unit} · layer: {self.layer} · classification: {self.classification}",
            f"  purpose: {self.purpose}",
            f"  usage: {self.usage}",
            f"  limitations: {self.limitations}",
            "  columns:",
        ]
        lines += [
            f"    - {c['name']:<28} {c['type']:<10} {c['sharing']:<11}{' personal' if c['personal'] else ''}"
            f"  {c['description']}"
            for c in self.columns
        ]
        return "\n".join(lines)


@dataclass
class PublishedEdition:
    product: str
    period: str
    datapackage: dict
    folder: Path
    period_column: str = "period"
    files_url: str | None = None  # publication storage of the edition's files, when they are not local
    project: Project | None = None  # whose readers open the publication storage

    @property
    def provenance(self) -> dict:
        x = self.datapackage["x-statera"]
        return {
            "product": x["product"],
            "period": x["period"],
            "cut_date": x["cutDate"],
            "version": self.datapackage["version"],
            "run": x.get("run"),
            "code_version": x.get("codeVersion"),
            "corrects": (x.get("corrects") or {}).get("version"),
            # cut of each internal product read: {product: run that released the cut}
            "cuts": {name: c["run"] for name, c in (x.get("cuts") or {}).items()},
        }

    def ports(self) -> list[str]:
        return [r["name"] for r in self.datapackage["resources"] if r["format"] == "csv"]

    def table(self, port: str) -> ibis.Table:
        """The file exactly as it was published; the Data Package hash is checked."""
        resource = next((r for r in self.datapackage["resources"] if r["name"] == port), None)
        if resource is None:
            raise KeyError(f"port '{port}' is not in the edition; available: {self.ports()}")
        where, content = self._read(resource["path"])
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual != resource["hash"]:
            raise ValueError(
                f"{where}: hash differs from the recorded one ({resource['hash']}); file changed after publication"
            )
        return ibis.memtable(pd.read_csv(io.BytesIO(content), dtype={self.period_column: str}))

    def _read(self, path: str) -> tuple[str, bytes]:
        """The published file: local (production folder) or from the publication storage."""
        local = self.folder / path
        if local.exists():
            return str(local), local.read_bytes()
        if not self.files_url:
            raise FileNotFoundError(
                f"the files of {self.product} {self.period} are not here ({local}) and the series declares no "
                f"publication storage (files_url in {self.product}/7-disseminate/series.yaml)"
            )
        base = self.files_url.format(product=self.product, period=self.period)
        url = base.rstrip("/") + "/" + path
        return url, connection.read_bytes(url, self.project or current())


class Catalog:
    def __init__(self, project: Project, role: str | None = None, engine: str | None = None):
        self.project = project
        self.role = role
        self.engine = engine
        self._contracts = {contracts.physical_table(c): (path, c) for path, c in contracts.all_(project).values()}
        self._con = None

    @property
    def _cut_column(self) -> str:
        return self.project.conventions["cut_date_column"]

    @property
    def con(self):
        if self._con is None:
            self._con = connection.connect(self.project, self.engine)
        return self._con

    @property
    def source(self) -> str:
        """Which engine this catalog reads (credentials hidden)."""
        return connection.describe(self.project, self.engine)

    # --- discovery ----------------------------------------------------------------------------
    def search(
        self, theme: str | None = None, layer: str | None = None, product: str | None = None, text: str | None = None
    ) -> pd.DataFrame:
        """Catalog tables, filtered by theme, layer, product or text (in the name and in the purpose)."""
        themes = {p.name: (p.read("1-specify/product.odps.yaml") or {}) for p in self.project.products()}
        themes = {
            n: {c["property"]: c["value"] for c in o.get("customProperties", [])}.get("theme")
            for n, o in themes.items()
        }
        lines = []
        for table, (path, c) in sorted(self._contracts.items()):
            unit = self.project.unit_of(path)
            line = {
                "table": table,
                "layer": contracts.layer(c),
                "unit": unit.name if unit else "",
                "theme": themes.get(unit.name) if unit else None,
                "classification": classification.summary(classification.of_table(c)),
                "purpose": " ".join(c["description"]["purpose"].split()),
            }
            if layer and line["layer"] != layer:
                continue
            if product and line["unit"] != product:
                continue
            if theme and line["theme"] != theme:
                continue
            if text and text.lower() not in (table + line["purpose"]).lower():
                continue
            lines.append(line)
        return pd.DataFrame(lines)

    def _contract(self, table: str) -> dict:
        if table not in self._contracts:
            raise KeyError(f"'{table}' has no contract in the catalog; tables: {sorted(self._contracts)}")
        return self._contracts[table][1]

    def describe(self, name: str) -> Description:
        table = name
        c = self._contract(table)
        unit = self.project.unit_of(self._contracts[table][0])
        columns = [
            {
                "name": p["name"],
                "type": p.get("logicalType", ""),
                "sharing": p.get("classification", ""),
                "personal": bool(classification.personal_column(p)),
                "description": p.get("description", ""),
            }
            for p in c["schema"][0]["properties"]
        ]
        return Description(
            table=table,
            urn=contracts.custom_property_of(c, "urn") or "",
            unit=unit.name if unit else "",
            layer=contracts.layer(c) or "",
            purpose=" ".join(c["description"]["purpose"].split()),
            usage=" ".join(c["description"].get("usage", "").split()),
            limitations=" ".join(c["description"].get("limitations", "").split()),
            classification=classification.summary(classification.of_table(c)),
            columns=columns,
        )

    def notes(self, name: str) -> list[str]:
        """What to mind when reading the table, from what its contract declares: what a row is, how its
        period and cut-off date columns are read, and its cuts. The copilot's cards and the catalog page
        show the same notes."""
        c = self._contract(name)
        columns = [p["name"] for p in c["schema"][0]["properties"]]
        conventions, notes = self.project.conventions, []
        if grain := c["schema"][0].get("dataGranularityDescription"):
            notes.append(f"grain: {' '.join(str(grain).split())}.")
        if classification.of_table(c).get("nature") == "aggregated":
            notes.append(
                "aggregated: a row is a group, not an individual; add up its measures (sum), count(*) counts groups."
            )
        if conventions["period_column"] in columns:
            column = conventions["period_column"]
            notes.append(f"`{column}` is the reference period, text YYYY-MM (a whole year: {column} like '2026-%').")
        if conventions["cut_date_column"] in columns:
            notes.append(f"`{conventions['cut_date_column']}` is when the data was cut off, not a period to filter by.")
        if contracts.custom_property_of(c, "partitionedBy"):
            notes.append(
                f"One partition per cut: read ONE cut ({conventions['cut_date_column']} = (select "
                f"max({conventions['cut_date_column']}) from {name})), never add up across cuts."
            )
        return notes

    # --- reading --------------------------------------------------------------------------------
    def _caution(self, table: str, c: dict) -> str | None:
        dims = classification.of_table(c)
        if dims.get("sharing") != "broad" or dims.get("nature") in ("personal", "sensitive-personal"):
            return (
                f"{table} is {classification.summary(dims)}: internal use, under the contract's rules; "
                "aggregate and apply disclosure control before sharing any result."
            )
        return None

    def _warn(self, table: str, c: dict) -> None:
        if caution := self._caution(table, c):
            warnings.warn(caution, ClassificationWarning, stacklevel=3)

    def check_access(self, name: str) -> str | None:
        """What `table` checks before reading, for someone who writes their own SQL (`sql`): the table has a
        contract and the role may read it (else KeyError, AccessDenied). Returns the classification
        caution, when the table is not for broad sharing."""
        c = self._contract(name)
        self._authorize(name)
        return self._caution(name, c)

    def _authorize(self, table: str) -> None:
        """With a role (someone outside the owner team), reading an internal product requires a valid authorization."""
        if not self.role:
            return
        from statera import ports

        owner = self.project.unit_of(self._contracts[table][0])
        if owner is None or not owner.internal:
            return
        if not ports.current_authorization(owner, self.role):
            raise AccessDenied(
                f"role '{self.role}' has no valid authorization to read {table} (internal product "
                f"{owner.name}). Use a cataloged query or request authorization, with purpose and term, "
                "in the product's authorizedConsumers."
            )

    def cuts(self, name: str) -> list[str]:
        """RELEASED cuts of the table (manifests of the owner internal product), from oldest to newest."""
        table = name
        from statera.collect import released_cuts

        owner = self.project.unit_of(self._contracts[table][0])
        return released_cuts(owner) if owner and owner.internal else []

    def table(self, name: str, cut_date: str | None = None) -> ibis.Table:
        """Ibis table. Without cut_date: the current data (in a per-cut table, the last RELEASED cut).
        With cut_date: bronze rebuilt at that cut, or that cut's partition."""
        table = name
        c = self._contract(table)
        self._authorize(table)
        self._warn(table, c)
        schema, name = table.split(".")
        if cut_date and contracts.layer(c) == "bronze":
            return self._bronze(table, c, name, cut_date)
        t = self.con.table(name, database=schema)
        partition = contracts.custom_property_of(c, "partitionedBy")
        if partition and not cut_date:
            released = self.cuts(table)
            if not released and not connection.is_local(self.project, self.engine):
                raise CutNotAvailable(
                    f"{table} is partitioned by cut, and the released cuts are recorded where the data is "
                    f"produced, not here: give cut_date (cuts in the engine: {_cut_dates(t, self._cut_column)})"
                )
            if not released:
                raise CutNotAvailable(f"{table}: no cut released yet")
            cut_date = released[-1]
        if cut_date:
            if self._cut_column not in t.columns:
                raise CutNotAvailable(f"{table} does not record a cut-off date")
            released = self.cuts(table)
            if partition and released and cut_date not in released:
                # partition computed but not released (e.g. blocked in validation): not read
                raise CutNotAvailable(f"{table}: cut {cut_date} was not released (released: {released})")
            filtered = t.filter(t[self._cut_column] == ibis.literal(cut_date).cast("date"))
            if filtered.count().execute() == 0:
                available = self.cuts(table) or _cut_dates(t, self._cut_column)
                raise CutNotAvailable(
                    f"{table} at cut {cut_date} is not available (cuts: {available}). Dissemination "
                    "tables keep only the latest processing; for the published figure, use cat.edition(...)."
                )
            return filtered
        return t

    def _bronze(self, table: str, contract: dict, name: str, cut_date: str) -> ibis.Table:
        """Bronze at the cut: batches up to the manifest's and reception up to the date. Without a manifest,
        rebuilds by reception date (with a warning: a late batch could change the result)."""
        from statera.collect import extraction, read_cut

        source = next(f for f in extraction.sources(self.project) if f.table == name)
        manifest = read_cut(source.product, cut_date)
        max_batch = ((manifest or {}).get("sources") or {}).get(name, {}).get("batch")
        if max_batch is None:
            warnings.warn(f"{table}: cut {cut_date} was not marked; rebuilt by reception date only",
                          ClassificationWarning, stacklevel=3)  # fmt: skip
        files = [
            str(source.batch_file(m["batch"])) for m in source.batches() if max_batch is None or m["batch"] <= max_batch
        ]
        if not files:
            raise CutNotAvailable(f"{table}: no batch extracted up to {cut_date}")
        t = self.con.read_parquet(files, table_name=f"{name}_{cut_date.replace('-', '')}", hive_partitioning=False)
        return t.filter(t[source.received_at_column].cast("date") <= ibis.literal(cut_date).cast("date"))

    def provenance(self, name: str) -> dict:
        table = name
        c = self._contract(table)
        t = self.con.table(table.split(".")[1], database=table.split(".")[0])
        col = self._cut_column
        cuts = self.cuts(table) or (_cut_dates(t, col) if col in t.columns else [])
        return {
            "table": table,
            "urn": contracts.custom_property_of(c, "urn"),
            "contract": c["id"],
            "contract_version": c["version"],
            "cut_dates": cuts,
            "engine": self.project.data_namespace,
            "source": self.source,
        }

    def edition(self, product: str, period: str) -> PublishedEdition:
        unit = self.project.product(product)
        path = registry.edition_path(unit, period)
        if not path.exists():
            raise LookupError(f"{product} {period} was not published (no record at {path})")
        return PublishedEdition(
            product,
            period,
            json.loads(path.read_text(encoding="utf-8")),
            unit.data(7) / period,
            self.project.conventions["period_column"],
            registry.series_config(unit).get("files_url") if registry.series_file(unit).exists() else None,
            self.project,
        )

    def editions(self, product: str) -> pd.DataFrame:
        path = registry.index_path(self.project.product(product))
        return pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()

    # --- metrics, queries and SQL ----------------------------------------------------------------
    def metrics(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"metric": n, "label": m["label"], "type": m["type"], "description": m["description"]}
                for n, m in metrics.definitions(self.project).items()
            ]
        )

    def metric(
        self,
        name: str,
        by: list[str] | None = None,
        period: str | None = None,
        periods: tuple[str, str] | None = None,
        filters: dict | None = None,
    ) -> pd.DataFrame:
        """Official metric (metrics.yaml): grouped by `by`, for one period or a range (`periods`)."""
        return metrics.compute(name, by, period, periods, filters, self.project, self.engine)

    def queries(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "id": c.id,
                    "title": c.title,
                    **{e: c.meta.get(e) for e in ("theme", "product", "frequency", "purpose", "status")},
                }
                for c in queries.load(self.project)
            ]
        )

    def query(self, query_id: str, **params) -> pd.DataFrame:
        """Runs a cataloged query; the parameters are those declared in its query.yaml."""
        return queries.run(query_id, params, project=self.project, engine=self.engine)

    def sql(self, text: str) -> pd.DataFrame:
        """Raw SQL on the engine (read-only)."""
        return connection.run_sql(self.con, text)

    def close(self) -> None:
        if self._con is not None:
            self._con.disconnect()
            self._con = None


def _cut_dates(t: ibis.Table, column: str) -> list[str]:
    return sorted(t.select(d=t[column].cast("string")).distinct().to_pandas()["d"].tolist())


def open(project: Project | None = None, role: str | None = None, engine: str | None = None) -> Catalog:
    """Opens the catalog. `role` (or STATERA_ROLE) identifies someone outside the team that owns the
    internal products: reading them then requires a valid authorization. In production, the reader is
    identified by their engine login, and the engine's permissions (generated grants) are the real boundary.
    `engine`: where to read the data (statera.consume.connection); by default, the shared engine declared
    in statera.toml, else the local one. The connection opens on the first read."""
    import os

    return Catalog(project or current(), role or os.environ.get("STATERA_ROLE") or None, engine)
