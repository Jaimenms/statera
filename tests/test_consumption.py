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
    _write(root / "queries/test/by-uf/query.yaml", QUERY)
    _write(
        root / "queries/test/by-uf/by_uf.sql",
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


EDITION = b"period,uf,value\n2026-01,SP,10\n"


def _published(project, files_url: str) -> None:
    """An edition of `sample` in the register, whose files live only in the publication storage."""
    _write(
        project.product("sample").phase(7) / "series.yaml",
        f"""
        files_url: "{files_url}"
        edition_registry: {{edition: "sample/{{period}}.datapackage.json", index: sample/editions.csv}}
        """,
    )
    resource = {"name": "by-uf", "path": "by-uf.csv", "format": "csv"}
    package = {
        "version": "1.0.0",
        "resources": [{**resource, "hash": f"sha256:{hashlib.sha256(EDITION).hexdigest()}"}],
        "x-statera": {"product": "sample", "period": "2026-01", "cutDate": "2026-02-20"},
    }
    record = project.edition_registry / "sample" / "2026-01.datapackage.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(package), encoding="utf-8")


def test_edition_from_the_publication_storage(readable, tmp_path):
    storage = tmp_path / "storage" / "2026-01"
    storage.mkdir(parents=True)
    (storage / "by-uf.csv").write_bytes(EDITION)
    _published(readable, str(tmp_path / "storage" / "{period}"))
    edition = catalog.open(readable).edition("sample", "2026-01")
    assert edition.table("by-uf").to_pandas()["value"].tolist() == [10]
    (storage / "by-uf.csv").write_bytes(EDITION + b"2026-01,RJ,1\n")
    with pytest.raises(ValueError, match="hash differs"):
        edition.table("by-uf")


BUCKET_READER = '''
from pathlib import Path
from urllib.parse import urlsplit


def read(url, project):
    """bucket://name/key: here a folder per bucket; in production, boto3, gcsfs, azure-storage-blob…"""
    parts = urlsplit(url)
    return (Path(project.root) / "buckets" / parts.netloc / parts.path.lstrip("/")).read_bytes()
'''


def test_repository_reader_for_the_publication_storage(readable):
    _write(readable.root / "readers/bucket.py", BUCKET_READER)
    (readable.root / "buckets/pub/sample/2026-01").mkdir(parents=True)
    (readable.root / "buckets/pub/sample/2026-01/by-uf.csv").write_bytes(EDITION)
    readable.config["consumption"] = {"readers": {"bucket": "readers/bucket.py:read"}}
    _published(readable, "bucket://pub/{product}/{period}/")
    edition = catalog.open(readable).edition("sample", "2026-01")
    assert edition.table("by-uf").to_pandas()["value"].tolist() == [10]
    (readable.root / "buckets/pub/sample/2026-01/by-uf.csv").write_bytes(EDITION + b"x\n")
    with pytest.raises(ValueError, match="hash differs"):  # whatever read it, the hash is checked
        edition.table("by-uf")


def test_package_reader_and_unknown_storage(readable, monkeypatch):
    _published(readable, "s3://pub/{product}/{period}/")
    edition = catalog.open(readable).edition("sample", "2026-01")
    with pytest.raises(connection.PublicationNotAvailable, match="declare a reader for 's3'"):
        edition.table("by-uf")
    seen = []
    points = {connection.READER_ENTRY_POINTS: [_Point("s3", lambda url, project: seen.append(url) or EDITION)]}
    monkeypatch.setattr(connection, "entry_points", lambda group: points.get(group, []))
    assert edition.table("by-uf").to_pandas()["value"].tolist() == [10]
    assert seen == ["s3://pub/sample/2026-01/by-uf.csv"]
    points[connection.READER_ENTRY_POINTS] = [_Point("s3", lambda url, project: "text")]
    with pytest.raises(connection.PublicationNotAvailable, match="must return bytes"):
        edition.table("by-uf")


# --- connectors: other sources are an extension of whoever uses Statera ------------------------------

PARQUET_CONNECTOR = '''
from pathlib import Path
from urllib.parse import urlsplit

import ibis


def connect(url, project):
    """parquet+dir:///folder: <folder>/<schema>/<table>.parquet as views (the same idea serves S3)."""
    folder = Path(urlsplit(url).path)
    con = ibis.duckdb.connect()
    for file in sorted(folder.glob("*/*.parquet")):
        con.raw_sql(f"create schema if not exists {file.parent.name}")
        con.raw_sql(f"create view {file.parent.name}.{file.stem} as select * from read_parquet('{file}')")
    return con
'''

SQLITE_CONNECTOR = '''
from urllib.parse import urlsplit

import ibis


def connect(url, project):
    """sqlite+gold:///file.db: a SQLite file read as the gold schema (another engine, other SQL)."""
    con = ibis.sqlite.connect()
    con.raw_sql(f"attach database '{urlsplit(url).path}' as gold")
    return con
'''


def _export_parquet(warehouse: Path, folder: Path) -> None:
    con = duckdb.connect(str(warehouse), read_only=True)
    (folder / "gold").mkdir(parents=True)
    con.execute(f"copy gold.sample_by_uf to '{folder / 'gold' / 'sample_by_uf.parquet'}' (format parquet)")
    con.close()


def test_repository_connector_for_parquet_files(readable, tmp_path):
    _export_parquet(_warehouse(tmp_path / "w.duckdb"), tmp_path / "lake")
    _write(readable.root / "connectors/parquet_dir.py", PARQUET_CONNECTOR)
    readable.config["consumption"] = {
        "engine": f"parquet+dir://{tmp_path / 'lake'}",
        "connectors": {"parquet+dir": "connectors/parquet_dir.py:connect"},
    }
    assert connection.connector_for(readable).origin == "statera.toml (connectors/parquet_dir.py:connect)"
    assert queries.run("by-uf", {"uf": "SP"}, project=readable)["value"].tolist() == [10, 7]
    assert catalog.open(readable).sql("select count(*) as n from gold.sample_by_uf where uf = 'RJ'")["n"][0] == 1


def test_repository_connector_for_another_engine(readable, tmp_path):
    import sqlite3

    db = tmp_path / "gold.db"
    with sqlite3.connect(db) as con:
        con.execute("create table sample_by_uf (period text, uf text, value integer, cut_date text)")
        con.executemany(
            "insert into sample_by_uf values (?, ?, ?, ?)",
            [
                ("2026-01", "SP", 10, "2026-02-20"),
                ("2026-01", "RJ", 4, "2026-02-20"),
                ("2026-02", "SP", 7, "2026-03-20"),
            ],
        )
    _write(readable.root / "connectors/sqlite_gold.py", SQLITE_CONNECTOR)
    readable.config["consumption"] = {
        "engine": f"sqlite+gold://{db}",
        "connectors": {"sqlite+gold": "connectors/sqlite_gold.py:connect"},
    }
    assert queries.run("by-uf", {"uf": "SP"}, project=readable)["value"].tolist() == [10, 7]
    # SQLite has no DuckDB parameters: the period and the filter go as literals quoted by sqlglot
    total = metrics.compute("total", ["uf"], period="2026-01", filters={"uf": "SP"}, project=readable)
    assert total["total"].tolist() == [10]
    assert catalog.open(readable).sql("select count(*) as n from gold.sample_by_uf")["n"][0] == 3


class _Point:
    """An installed package's entry point (importlib.metadata.EntryPoint), without installing one."""

    def __init__(self, name, function):
        self.name, self.value, self.function = name, f"acme.{name}:connect", function
        self.dist = type("Dist", (), {"name": "acme-connectors"})()

    def load(self):
        return self.function


def test_package_connectors_and_repository_precedence(readable, tmp_path, monkeypatch):
    shared = _warehouse(tmp_path / "w.duckdb")
    calls = []

    def package_connect(url, project):
        calls.append(url)
        return connection.ibis.duckdb.connect(str(shared), read_only=True)

    monkeypatch.setattr(
        connection,
        "entry_points",
        lambda group: [_Point("acme", package_connect)] if group == connection.ENTRY_POINTS else [],
    )
    readable.config["consumption"] = {"engine": "acme://warehouse/stats"}
    assert connection.connector_for(readable).origin == "package acme-connectors (acme.acme:connect)"
    assert queries.run("by-uf", {"uf": "RJ"}, project=readable)["value"].tolist() == [4]
    assert calls == ["acme://warehouse/stats"]
    _write(readable.root / "connectors/acme.py", "def connect(url, project):\n    return 'not a backend'\n")
    readable.config["consumption"]["connectors"] = {"acme": "connectors/acme.py:connect"}
    with pytest.raises(connection.EngineNotAvailable, match="must return an Ibis backend"):
        connection.connect(readable)  # the repository's connector wins over the package's


def test_unknown_scheme_points_to_the_extension(readable, monkeypatch):
    monkeypatch.setenv(connection.ENV, "h2://server/stats")
    with pytest.raises(connection.EngineNotAvailable, match=r"declare a connector for 'h2'"):
        connection.connect(readable)


def test_engines_ibis_knows_need_no_connector(readable, monkeypatch):
    """Amazon Athena (like PostgreSQL, Trino, MySQL…) is opened by Ibis from the URL: no code in Statera."""
    url = "athena://?s3_staging_dir=s3://results/statera/&region_name=us-east-1&work_group=analysts"
    monkeypatch.setenv(connection.ENV, url)
    seen = []
    monkeypatch.setattr(connection.ibis, "connect", lambda value: seen.append(value) or "backend")
    assert connection.connector_for(readable) is None
    assert connection.connect(readable) == "backend" and seen == [url]

    def refused(value):
        raise RuntimeError("An error occurred (UnrecognizedClientException): the security token is invalid")

    monkeypatch.setattr(connection.ibis, "connect", refused)
    with pytest.raises(connection.EngineNotAvailable, match="cannot connect to athena://: An error occurred"):
        connection.connect(readable)


def test_validation_checks_the_declared_connectors(readable):
    from statera import validation

    _write(
        readable.root / "connectors/ok.py", "import some_driver_only_readers_have\n\ndef connect(url, project): ...\n"
    )
    readable.config["consumption"] = {
        "connectors": {"ok": "connectors/ok.py:connect", "typo": "connectors/ok.py:conect", "gone": "nope.py:connect"},
        "readers": {"s3": "connectors/ok.py:read"},
    }
    findings = {f.message: f.ok for f in validation._connectors(readable)}
    assert findings == {
        "connector ok: connectors/ok.py:connect": True,
        "connector typo: connectors/ok.py has no function conect": False,
        "connector gone: 'nope.py:connect' is not an existing 'file.py:function'": False,
        "reader s3: connectors/ok.py has no function read": False,
    }


def test_the_compiled_sql_is_what_runs(readable, tmp_path):
    sql = queries.compiled_sql("by-uf", {"uf": "SP"}, project=readable)
    assert "{{" not in sql and "ref(" not in sql and "'SP'" in sql
    assert sql.splitlines()[0].startswith("-- ") and "uf=SP" in sql.splitlines()[0]
    with pytest.raises(queries.QueryError, match="not of type"):
        queries.compiled_sql("by-uf", {"uf": "S'P"}, project=readable)  # validated before reaching the SQL
    _warehouse(readable.warehouse)
    queries.run("by-uf", {"uf": "SP"}, output=tmp_path / "out", project=readable)
    assert (tmp_path / "out" / "query.sql").read_text(encoding="utf-8") == sql


def test_a_query_explains_its_parameters(readable, capsys):
    from statera import cli

    text = queries.describe("by-uf", project=readable)
    assert "Parameters (--param name=value):" in text and "uf: 2-letter UF code" in text and "required" in text
    assert "statera query run org.example.query.by-uf --param uf=SP --output result/" in text
    for action in ("show", "run"):
        assert cli.main(["query", action, "by-uf", "--help"]) == 0
        assert "2-letter UF code" in capsys.readouterr().out
    assert cli.main(["query", "show", "nonexistent", "-h"]) == 2
    with pytest.raises(queries.QueryError, match="statera query show org.example.query.by-uf --help"):
        queries.compiled_sql("by-uf", {}, project=readable)
