"""Reading the data without the production tools: relations without dbt, the shared engine, editions
from the publication storage."""

import hashlib
import json
import textwrap
from pathlib import Path

import duckdb
import pytest

from statera.consume import catalog, connection, metrics, queries
from statera.process import dbt

QUERY = """
id: org.example.query.by-uf
title: By UF
description: test
theme: test
bases: [gold.sample_by_uf]
frequency: on-demand
purpose: study
parameters:
  - {name: uf, type: uf, required: true}
limitations: none
output_classification: {sharing: broad, nature: aggregated, secrecy: statistical}
owner: team-x
status: cataloged
version: 0.1.0
"""

METRICS = """
metrics:
  - name: total
    label: Total
    description: Sum of the value
    type: simple
    aggregation: sum
    expression: value
    time: period
    sources:
      - {table: gold.sample_by_uf, dimensions: [uf]}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _warehouse(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("create schema gold; create schema ref")
    con.execute(
        "create table gold.sample_by_uf as select * from (values ('2026-01', 'SP', 10, date '2026-02-20'), "
        "('2026-01', 'RJ', 4, date '2026-02-20'), ('2026-02', 'SP', 7, date '2026-03-20')) "
        "t(period, uf, value, cut_date)"
    )
    con.execute("create table ref.ref_uf as select 'SP' as uf, 'São Paulo' as name")
    con.close()
    return path


@pytest.fixture
def readable(minimal_project, monkeypatch):
    """The minimal project with a gold model, a reference table, a query and a metric."""
    monkeypatch.delenv(connection.ENV, raising=False)
    root = minimal_project.root
    _write(root / "products/sample/5-process/models/marts/sample/sample_by_uf.sql", "select 1")
    _write(root / "products/sample/5-process/models/intermediate/sample/int_sample__x.py", "def model(dbt, s): ...")
    _write(root / "references/uf/ref_uf.csv", "uf,name\nSP,São Paulo\n")
    _write(
        root / "references/uf/ref_uf.odcs.yaml",
        """
        kind: DataContract
        id: ref-uf
        version: 1.0.0
        description: {purpose: UFs}
        schema:
          - name: ref_uf
            physicalName: ref.ref_uf
            properties: [{name: uf, logicalType: string}]
        """,
    )
    _write(root / "products/sample/queries/by-uf/query.yaml", QUERY)
    _write(
        root / "products/sample/queries/by-uf/by_uf.sql",
        "select period, value from {{ ref('sample_by_uf') }} where uf = '{{ var(\"uf\") }}' order by period",
    )
    _write(root / "products/sample/5-process/metrics.yaml", METRICS)
    from statera.project import current

    return current()


def test_relations_follow_dbt_rules_without_dbt(readable):
    relations = dbt.relations(readable)
    assert relations["sample_by_uf"] == "gold.sample_by_uf"
    assert relations["int_sample__x"] == "silver.int_sample__x"
    assert relations["ref_uf"] == "ref.ref_uf"


def test_manifest_relations_use_schema_and_alias():
    manifest = {
        "nodes": {
            "model.x.a": {"resource_type": "model", "name": "a", "schema": "gold", "alias": "a_v2"},
            "seed.x.b": {"resource_type": "seed", "name": "b", "schema": "ref", "alias": None},
            "analysis.x.q": {"resource_type": "analysis", "name": "q", "schema": "main"},
        }
    }
    assert dbt.manifest_relations(manifest) == {"a": "gold.a_v2", "b": "ref.b"}


def test_engine_order(readable, monkeypatch):
    assert connection.location(readable) == connection.LOCAL
    readable.config.setdefault("consumption", {})["engine"] = "https://data.example.org/w.duckdb"
    assert connection.location(readable) == "https://data.example.org/w.duckdb"
    monkeypatch.setenv(connection.ENV, "postgres://reader:secret@db.example.org:5432/stats")
    assert connection.describe(readable) == "postgres://reader:***@db.example.org:5432/stats"
    assert connection.location(readable, engine="local") == connection.LOCAL


def test_no_data_says_what_to_do(readable):
    with pytest.raises(connection.EngineNotAvailable, match="shared engine"):
        queries.run("by-uf", {"uf": "SP"}, project=readable)


def test_query_and_metric_on_the_local_engine(readable):
    _warehouse(readable.warehouse)
    df = queries.run("by-uf", {"uf": "SP"}, project=readable)
    assert df["value"].tolist() == [10, 7]
    assert df.attrs["provenance"]["engine"].startswith("local")
    total = metrics.compute("total", ["uf"], period="2026-01", project=readable)
    assert dict(zip(total["uf"], total["total"], strict=True)) == {"RJ": 4, "SP": 10}
    assert total.attrs["provenance"]["cut_dates"] == ["2026-02-20", "2026-03-20"]


def test_shared_duckdb_file_by_path_and_attached(readable, tmp_path, monkeypatch):
    shared = _warehouse(tmp_path / "share" / "warehouse.duckdb")
    monkeypatch.setenv(connection.ENV, str(shared))
    assert queries.run("by-uf", {"uf": "RJ"}, project=readable)["value"].tolist() == [4]
    # a remote file (https://, s3://) is ATTACHed read-only; a local file exercises the same path
    con = connection.ibis.duckdb.connect()
    connection._attach(con, str(shared))
    assert con.table("sample_by_uf", database="gold").count().execute() == 3
    assert connection.run_sql(con, "select count(*) as n from ref.ref_uf")["n"].tolist() == [1]
    with pytest.raises(duckdb.Error):
        con.raw_sql("create table gold.x as select 1")  # read-only


def test_other_engines_get_quoted_literals():
    sql = connection._inline("select * from t where uf = ? and name = ?", ["SP", "O'Brien"], "postgres")
    assert sql == "select * from t where uf = 'SP' and name = 'O''Brien'"
    with pytest.raises(ValueError):
        connection._inline("select ?", [], "postgres")


def test_catalog_reads_the_shared_engine(readable, tmp_path, monkeypatch):
    shared = _warehouse(tmp_path / "share" / "warehouse.duckdb")
    _warehouse(readable.warehouse)
    readable.config.setdefault("consumption", {})["engine"] = str(shared)
    cat = catalog.open(readable)
    assert cat.source == str(shared)
    assert cat.sql("select count(*) as n from gold.sample_by_uf")["n"].tolist() == [3]
    assert catalog.open(readable, engine="local").source.startswith("local")


def test_edition_from_the_publication_storage(readable, tmp_path):
    unit = readable.product("sample")
    storage = tmp_path / "storage" / "2026-01"
    storage.mkdir(parents=True)
    content = b"period,uf,value\n2026-01,SP,10\n"
    (storage / "by-uf.csv").write_bytes(content)
    _write(
        unit.phase(7) / "series.yaml",
        f"""
        files_url: "{tmp_path / "storage" / "{period}"}"
        edition_registry: {{edition: "sample/{{period}}.datapackage.json", index: sample/editions.csv}}
        """,
    )
    package = {
        "version": "1.0.0",
        "resources": [
            {
                "name": "by-uf",
                "path": "by-uf.csv",
                "format": "csv",
                "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
            }
        ],
        "x-statera": {"product": "sample", "period": "2026-01", "cutDate": "2026-02-20"},
    }
    record = readable.edition_registry / "sample" / "2026-01.datapackage.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(package), encoding="utf-8")
    edition = catalog.open(readable).edition("sample", "2026-01")
    assert edition.table("by-uf").to_pandas()["value"].tolist() == [10]
    (storage / "by-uf.csv").write_bytes(content + b"2026-01,RJ,1\n")
    with pytest.raises(ValueError, match="hash differs"):
        edition.table("by-uf")
