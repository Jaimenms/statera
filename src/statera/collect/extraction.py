"""Incremental extraction: source system -> bronze layer, driven by the contracts (GSBPM 4.3).

Sources are not in the code: each bronze contract declares where it comes from and how to date the extraction.

    customProperties:
      - property: extraction
        value: {source: events.parquet, receivedAtColumn: received_at, sourceSystem: ERP}

Each run writes an immutable BATCH with what was received since the last load
(`bronze/<table>/batch=000001/part-0.parquet` + `_metadata.json` with the reception window, rows and
hash). Each record is stored once. A CUT is not a copy: it is a mark over the batches,
"received up to the cut-off date and present up to batch L", recorded in an immutable manifest. So a
late-arriving batch does not change an already marked cut, and any date can be reconstructed.

In the demo, the "source" is a Parquet file in `<product>/4-collect/data/source/`; in production, a
connector to the source system and, for storage, Apache Iceberg tables with tags.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

from statera import contracts
from statera.edition import now
from statera.project import Project, Unit, current


@dataclass(frozen=True)
class Source:
    contract_id: str
    table: str  # table name in the bronze schema
    origin: str
    received_at_column: str
    system: str
    description: str
    product: Unit

    @property
    def source_file(self) -> Path:
        return self.product.data(4) / "source" / self.origin

    @property
    def bronze_folder(self) -> Path:
        return self.product.data(4) / "bronze" / self.table

    def batches(self) -> list[dict]:
        """Metadata of the batches already extracted, in order."""
        files = sorted(self.bronze_folder.glob("batch=*/_metadata.json"))
        return [json.loads(a.read_text(encoding="utf-8")) for a in files]

    def batch_file(self, number: int) -> Path:
        return self.bronze_folder / f"batch={number:06d}" / "part-0.parquet"


def sources(project: Project | None = None, unit: Unit | None = None) -> list[Source]:
    """Sources declared in the bronze contracts (of the whole project, or of one source-aligned product)."""
    project = project or current()
    output = []
    for path, contract in contracts.all_(project).values():
        extraction = contracts.custom_property_of(contract, "extraction")
        owner = project.unit_of(path)
        if contracts.layer(contract) != "bronze" or not extraction or (unit and owner != unit):
            continue
        output.append(
            Source(
                contract_id=contract["id"],
                table=contracts.physical_table(contract).split(".")[-1],
                origin=extraction["source"],
                received_at_column=extraction["receivedAtColumn"],
                system=extraction["sourceSystem"],
                description=contract["name"],
                product=owner,
            )
        )
    return sorted(output, key=lambda f: f.table)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract_increment(cut_date: date, sources_: list[Source]) -> list[dict]:
    """New batch with what was received between the last load and the cut-off date (if any)."""
    results = []
    con = duckdb.connect()
    try:
        for source in sources_:
            if not source.source_file.exists():
                raise FileNotFoundError(f"Source {source.source_file} not found. Run first: statera synthetic")
            batches = source.batches()
            mark = max((date.fromisoformat(m["window_end"]) for m in batches), default=None)
            if mark is not None and cut_date <= mark:
                results.append({"source": source.table, "batch": None, "rows": 0, "watermark": mark.isoformat()})
                continue
            number = len(batches) + 1
            file = source.batch_file(number)
            file.parent.mkdir(parents=True, exist_ok=True)
            col = f"CAST({source.received_at_column} AS DATE)"
            window = f"{col} <= DATE '{cut_date.isoformat()}'"
            if mark is not None:
                window += f" AND {col} > DATE '{mark.isoformat()}'"
            con.execute(
                f"COPY (SELECT *, CAST({number} AS INTEGER) AS batch FROM read_parquet('{source.source_file}') "
                f"WHERE {window}) TO '{file}' (FORMAT parquet)"
            )
            meta = {
                "source": source.table,
                "contract": source.contract_id,
                "sourceSystem": source.system,
                "batch": number,
                "window_start": mark.isoformat() if mark else None,
                "window_end": cut_date.isoformat(),
                "rows": con.execute(f"SELECT count(*) FROM read_parquet('{file}')").fetchone()[0],
                "sha256": _sha256(file),
                "extracted_at": now(),
            }
            (file.parent / "_metadata.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            results.append(meta)
    finally:
        con.close()
    return results


def source_manifest(cut_date: date, sources_: list[Source]) -> dict:
    """For each source: the cut's maximum batch, the rows in the cut and the hashes of the batches used."""
    output = {}
    con = duckdb.connect()
    try:
        for source in sources_:
            batches = source.batches()
            covering = [m for m in batches if date.fromisoformat(m["window_end"]) >= cut_date]
            if not covering:
                raise ValueError(f"{source.table}: no batch covers the cut-off date {cut_date} (extract first)")
            max_batch = min(m["batch"] for m in covering)
            in_use = [m for m in batches if m["batch"] <= max_batch]
            files = ", ".join(f"'{source.batch_file(m['batch'])}'" for m in in_use)
            lines = con.execute(
                f"SELECT count(*) FROM read_parquet([{files}]) "
                f"WHERE CAST({source.received_at_column} AS DATE) <= DATE '{cut_date.isoformat()}'"
            ).fetchone()[0]
            signature = hashlib.sha256("".join(m["sha256"] for m in in_use).encode()).hexdigest()
            output[source.table] = {
                "contract": source.contract_id,
                "sourceSystem": source.system,
                "cutDate": cut_date.isoformat(),
                "batch": max_batch,
                "rows": lines,
                "batches": [{"batch": m["batch"], "sha256": m["sha256"]} for m in in_use],
                "sha256": signature,
            }
    finally:
        con.close()
    return output


def publish_bronze_to_warehouse(sources_: list[Source], project: Project | None = None) -> None:
    """Exposes all bronze batches as views in the engine's `bronze` schema (without copying the data)."""
    project = project or current()
    project.warehouse.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(project.warehouse))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        for source in sources_:
            pattern = source.bronze_folder / "batch=*" / "part-0.parquet"
            con.execute(
                f"CREATE OR REPLACE VIEW bronze.{source.table} AS "
                f"SELECT * FROM read_parquet('{pattern}', union_by_name = true, hive_partitioning = false)"
            )
    finally:
        con.close()
