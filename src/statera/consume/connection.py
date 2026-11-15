"""Where the data is read from, for those who query it.

Production writes to the LOCAL engine (`[engine]` in statera.toml: the DuckDB file that `statera run`
builds). Someone outside the data team has no local engine and should not need one: they read a SHARED
engine that the data team keeps up to date, declared in statera.toml:

    [consumption]
    engine = "s3://statistics/warehouse.duckdb"           # a DuckDB file: a path (network share), https://, s3://…
    engine = "postgres://reader@db.example.org/statistics"  # or any engine Ibis connects to
                                                           # (extras statera[postgres], statera[trino])

Which engine is read, in order: the `engine` argument (`catalog.open(engine=…)`, `--engine`), the
STATERA_ENGINE variable (for a URL with credentials, which never goes into git), `[consumption] engine`,
and the local engine. The value `local` forces the local engine (the data team checking what it has just
produced). A DuckDB file is always opened READ-ONLY; in other engines, the reader's login and the grants
generated from the contracts are the boundary.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import ibis
import pandas as pd

from statera.project import Project

ENV = "STATERA_ENGINE"
LOCAL = "local"
ATTACHED = "warehouse"  # catalog name of an attached DuckDB file
URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
DUCKDB_FILE = re.compile(r"\.(duckdb|ddb|db)$", re.IGNORECASE)


class EngineNotAvailable(RuntimeError):
    """There is no data to read: no local engine and no shared one declared."""


def location(project: Project, engine: str | None = None) -> str:
    """`local` or the shared engine's location, by the order in the module docstring."""
    value = engine or os.environ.get(ENV) or project.consumption.get("engine") or LOCAL
    return str(value).strip()


def is_local(project: Project, engine: str | None = None) -> bool:
    return location(project, engine) == LOCAL


def describe(project: Project, engine: str | None = None) -> str:
    """The engine read, for provenance and messages (a password in the URL is hidden)."""
    value = location(project, engine)
    if value == LOCAL:
        return f"local ({project.warehouse})"
    parts = urlsplit(value)
    if parts.password:
        netloc = f"{parts.username}:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        value = urlunsplit(parts._replace(netloc=netloc))
    return value


def _missing_local(project: Project) -> EngineNotAvailable:
    return EngineNotAvailable(
        f"there is no data to read: the local engine ({project.warehouse}) does not exist and statera.toml "
        "declares no shared engine ([consumption] engine). If you only want to query the data, ask the data "
        f"team for the shared engine's location and set it in {ENV} (or in [consumption] engine). If you "
        "produce the data, `statera run` builds the local engine."
    )


def _local_path(project: Project, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (project.root / path).resolve()


def _attach(con, value: str) -> None:
    """Attaches a remote DuckDB file read-only and makes it the default catalog (so `gold.x` resolves)."""
    if value.lower().startswith("s3://"):
        # AWS credentials from the usual chain (variables, profile, role); a public bucket needs none
        with contextlib.suppress(Exception):
            con.raw_sql("CREATE SECRET IF NOT EXISTS statera_s3 (TYPE s3, PROVIDER credential_chain)")
    quoted = value.replace("'", "''")
    con.raw_sql(f"ATTACH '{quoted}' AS {ATTACHED} (READ_ONLY)")
    con.raw_sql(f"USE {ATTACHED}")


def connect(project: Project, engine: str | None = None):
    """Ibis connection to the engine, read-only."""
    value = location(project, engine)
    if value == LOCAL:
        if not project.warehouse.exists():
            raise _missing_local(project)
        return ibis.duckdb.connect(str(project.warehouse), read_only=True)
    if not URL.match(value) or value.lower().startswith("file://"):
        path = _local_path(project, value.removeprefix("file://"))
        if not path.exists():
            raise EngineNotAvailable(f"the shared engine {path} does not exist (or is not reachable from here)")
        return ibis.duckdb.connect(str(path), read_only=True)
    if DUCKDB_FILE.search(urlsplit(value).path):
        con = ibis.duckdb.connect()
        _attach(con, value)
        return con
    return ibis.connect(value)


def _inline(sql: str, params: list, dialect: str) -> str:
    """`?` placeholders replaced by literals quoted by sqlglot, for engines without DuckDB's parameters."""
    import sqlglot

    pieces = sql.split("?")
    if len(pieces) - 1 != len(params):
        raise ValueError(f"{len(pieces) - 1} placeholders for {len(params)} parameters")
    output = pieces[0]
    for value, piece in zip(params, pieces[1:], strict=True):
        output += sqlglot.exp.convert(value).sql(dialect=dialect) + piece
    return output


def run_sql(con, sql: str, params: list | None = None) -> pd.DataFrame:
    """Runs the SQL on an open connection. DuckDB receives the parameters as parameters; other engines,
    as quoted literals."""
    if con.name == "duckdb":
        return con.con.execute(sql, params or []).df()
    return con.sql(_inline(sql, list(params or []), con.dialect) if params else sql).to_pandas()


def fetch(sql: str, params: list | None = None, project: Project | None = None, engine: str | None = None):
    """Runs read-only SQL on the engine and returns a DataFrame."""
    from statera.project import current

    project = project or current()
    con = connect(project, engine)
    try:
        return run_sql(con, sql, params)
    finally:
        con.disconnect()
