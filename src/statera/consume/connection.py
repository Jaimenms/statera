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

CONNECTORS: any other source is an extension of whoever uses Statera, not of Statera. A connector is a
function that receives the engine's URL and the project and returns an Ibis backend (`ibis.BaseBackend`),
which is all the catalog, the queries and the metrics use:

    def connect(url: str, project) -> ibis.BaseBackend: ...

It is chosen by the URL's scheme (`mariadb://`, `h2://`, `parquet+s3://`…), from, in order:

1. the repository, in statera.toml (a file of the content, like the other hooks):

       [consumption.connectors]
       parquet+s3 = "connectors/parquet_s3.py:connect"

2. an installed package, by its entry points (one package serves many repositories):

       [project.entry-points."statera.connectors"]
       h2 = "acme_connectors.h2:connect"

3. what Statera does by itself: a DuckDB file (path, https://, s3://), else `ibis.connect(url)`, which
   knows every engine Ibis has a backend for: MySQL, PostgreSQL, Trino, SQLite, Snowflake, BigQuery and
   Amazon Athena, among others (each needs its Ibis backend installed: statera[postgres], [trino], [athena]).
   Athena, for example, with the AWS credentials of the usual chain (never in the URL):

       athena://?s3_staging_dir=s3://my-results/statera/&region_name=us-east-1&work_group=analysts

A repository connector overrides a package's, and both override the built-in behavior for their scheme.
Opening the source read-only is the connector's responsibility.

READERS do the same for the publication storage of the editions (`files_url` in series.yaml): a function
`read(url, project) -> bytes`, in `[consumption.readers]` or in the entry points `statera.readers`. By
itself, Statera reads a path (network share), file:// and http(s)://; the hash in the register is checked
whatever read the file.
"""

from __future__ import annotations

import contextlib
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
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
ENTRY_POINTS = "statera.connectors"  # group of the installed packages' connectors
READER_ENTRY_POINTS = "statera.readers"  # group of the installed packages' publication storage readers


class EngineNotAvailable(RuntimeError):
    """There is no data to read: no local engine and no shared one declared, or the engine cannot be opened."""


class PublicationNotAvailable(RuntimeError):
    """A published file cannot be read from the publication storage."""


@dataclass(frozen=True)
class Connector:
    """A connector for one URL scheme, with where it was declared (shown in messages and in `doctor`)."""

    scheme: str
    origin: str
    load: Callable[[], Callable]


def _extensions(project: Project, group: str, section: str) -> dict[str, Connector]:
    """Extensions by scheme: the installed packages' (entry point `group`), overridden by the repository's
    (`[consumption.<section>]` in statera.toml, 'file.py:function')."""
    found = {}
    for point in entry_points(group=group):
        package = point.dist.name if point.dist else "?"
        found[point.name.lower()] = Connector(point.name.lower(), f"package {package} ({point.value})", point.load)
    for name, specification in (project.consumption.get(section) or {}).items():
        found[name.lower()] = Connector(
            name.lower(), f"statera.toml ({specification})", lambda s=specification: project.hook(s)
        )
    return found


def connectors(project: Project) -> dict[str, Connector]:
    """The connectors available, by scheme: the installed packages', overridden by the repository's."""
    return _extensions(project, ENTRY_POINTS, "connectors")


def readers(project: Project) -> dict[str, Connector]:
    """The readers of publication storage, by scheme: the installed packages', overridden by the repository's."""
    return _extensions(project, READER_ENTRY_POINTS, "readers")


def read_bytes(location: str, project: Project) -> bytes:
    """A published file from the publication storage (`files_url`). A READER is a function
    `read(url, project) -> bytes`, chosen by the URL's scheme like the connectors (repository, then
    installed packages); by itself, Statera reads a path (network share), file:// and http(s)://."""
    found = scheme(location)
    if found and (reader := readers(project).get(found)):
        try:
            content = reader.load()(location, project)
        except Exception as error:  # noqa: BLE001 (any failure of an extension is reported with its origin)
            raise PublicationNotAvailable(f"the reader for {found}:// ({reader.origin}) failed: {error}") from error
        if not isinstance(content, bytes):
            raise PublicationNotAvailable(
                f"the reader for {found}:// ({reader.origin}) must return bytes, not {type(content).__name__}"
            )
        return content
    if found in (None, "file"):
        return _local_path(project, location.removeprefix("file://")).read_bytes()
    if found in ("http", "https"):
        with urllib.request.urlopen(location, timeout=120) as response:  # noqa: S310 (declared in series.yaml)
            return response.read()
    raise PublicationNotAvailable(
        f"Statera does not read {found}:// by itself: declare a reader for '{found}' ([consumption.readers] in "
        f"statera.toml, or a package with a '{READER_ENTRY_POINTS}' entry point)"
    )


def scheme(value: str) -> str | None:
    """The URL's scheme (lowercase), or None for `local` and plain paths."""
    return urlsplit(value).scheme.lower() if URL.match(value) else None


def connector_for(project: Project, engine: str | None = None) -> Connector | None:
    """The extension connector that opens this engine, if any (None: Statera's own behavior)."""
    found = scheme(location(project, engine))
    return connectors(project).get(found) if found else None


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


def _through(connector: Connector, value: str, project: Project):
    try:
        con = connector.load()(value, project)
    except Exception as error:  # noqa: BLE001 (any failure of an extension is reported with its origin)
        raise EngineNotAvailable(
            f"the connector for {connector.scheme}:// ({connector.origin}) failed: {error}"
        ) from error
    if not isinstance(con, ibis.BaseBackend):
        raise EngineNotAvailable(
            f"the connector for {connector.scheme}:// ({connector.origin}) must return an Ibis backend "
            f"(ibis.BaseBackend), not {type(con).__name__}"
        )
    return con


def _ibis(value: str):
    found = scheme(value)
    try:
        return ibis.connect(value)
    except (ImportError, ValueError, AttributeError) as error:
        connector = (
            f"declare a connector for '{found}' ([consumption.connectors] in statera.toml, or a package with a "
            f"'{ENTRY_POINTS}' entry point)"
        )
        if found in {point.name for point in entry_points(group="ibis.backends")}:
            raise EngineNotAvailable(
                f"cannot open {found}://: its Ibis backend is not installed. Install it (statera[{found}] when "
                f"Statera has that extra, else ibis-framework[{found}]) or {connector}"
            ) from error
        raise EngineNotAvailable(f"Statera and Ibis do not know {found}://: {connector}") from error
    except Exception as error:  # noqa: BLE001 (the engine refused: credentials, network, permissions)
        first = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        raise EngineNotAvailable(f"cannot connect to {found}://: {first}") from error


def connect(project: Project, engine: str | None = None):
    """Ibis connection to the engine, read-only."""
    value = location(project, engine)
    if value == LOCAL:
        if not project.warehouse.exists():
            raise _missing_local(project)
        return ibis.duckdb.connect(str(project.warehouse), read_only=True)
    if connector := connector_for(project, engine):
        return _through(connector, value, project)
    if not URL.match(value) or value.lower().startswith("file://"):
        path = _local_path(project, value.removeprefix("file://"))
        if not path.exists():
            raise EngineNotAvailable(f"the shared engine {path} does not exist (or is not reachable from here)")
        return ibis.duckdb.connect(str(path), read_only=True)
    if DUCKDB_FILE.search(urlsplit(value).path):
        con = ibis.duckdb.connect()
        _attach(con, value)
        return con
    return _ibis(value)


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
