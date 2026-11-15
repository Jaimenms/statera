"""An ad hoc query, checked before anyone sees it and before it reads anything.

When no cataloged query or metric answers, the model writes SQL over the cataloged tables. `check` is
everything that must be true of it, cheapest first, each failure worded to go back to the model:

1. it parses (sqlglot, in the engine's dialect) as ONE statement;
2. it only reads: a SELECT, with nothing that writes inside and no function that reads files or other
   databases (the engine is opened read-only anyway; this is the second lock, not the first);
3. every table has a contract (`schema.table` as in the catalog) and the role may read it;
4. the engine accepts it: EXPLAIN binds every column and type without reading the data, so a column that
   does not exist comes back as the engine's own message, which usually names the right one.

Then `unknown_values`: a filter by a value no row has (`uf_abbr = 'São Paulo'`) returns nothing and looks
like an answer. Each text a query compares a column with is looked for in that column; when it is not
there, the model is told what is (`values`: the values a column really has, and the code that goes with a
name in another column of the same table). These are the only reads the copilot makes by itself: one value
at a time, never of a personal column, and never in a table that is not for broad sharing unless at least
FEW rows share the value (a category shows, an identifier does not).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import sqlglot
from sqlglot import exp

from statera.consume import connection

WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Copy)
FEW = 5  # in a table not for broad sharing, a value is shown only when at least this many rows have it
OUTSIDE = ("read_", "pg_read", "pg_ls", "dblink", "getenv", "glob", "query", "sniff_", "parquet_", "http_", "iceberg_")


class SqlError(ValueError):
    """Why the query cannot run, in words the model (or the person) can act on."""


@dataclass
class Checked:
    sql: str  # formatted, in the engine's dialect
    tables: list[str]
    cautions: list[str] = field(default_factory=list)  # tables that are not for broad sharing
    doubts: list[str] = field(default_factory=list)  # valid, but probably not what was meant (see `_doubts`)
    explained: bool = False  # the engine accepted it (False: no engine to ask here)


def dialect(catalog) -> str:
    try:
        return catalog.con.name
    except connection.EngineNotAvailable:
        return "duckdb"


def check(sql: str, catalog) -> Checked:
    name = dialect(catalog)
    sql = sql.strip().rstrip(";").strip()
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=name) if s is not None]
    except sqlglot.errors.SqlglotError as error:
        raise SqlError(f"the SQL does not parse: {str(error).splitlines()[0]}") from error
    if len(statements) != 1:
        raise SqlError(f"write exactly one SELECT statement (found {len(statements)})")
    tree = statements[0]
    if not isinstance(tree, exp.Query) or any(tree.find(kind) for kind in WRITES):
        raise SqlError("only a SELECT is allowed: the copilot reads, it never changes data")
    for function in tree.find_all(exp.Func):
        called = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).lower()
        if called.startswith(OUTSIDE):
            raise SqlError(f"the function {called}() is not allowed: read only the cataloged tables")

    names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables, cautions = [], []
    for node in tree.find_all(exp.Table):
        if not isinstance(node.this, exp.Identifier):
            raise SqlError(f"read only the cataloged tables, not {node.sql(dialect=name)}")
        if not node.db and node.name.lower() in names:
            continue
        table = f"{node.db}.{node.name}".lower()
        if node.catalog or table not in catalog._contracts:
            known = [t for t in catalog._contracts if t.split(".")[1] == node.name.lower()]
            hint = f"did you mean {known[0]}?" if known else "use a table of the catalog, written schema.table"
            raise SqlError(f"the table {node.sql(dialect=name)} is not in the catalog: {hint}")
        if table not in tables:
            tables.append(table)
            try:
                caution = catalog.check_access(table)
            except PermissionError as error:
                raise SqlError(str(error)) from error
            if caution:
                cautions.append(caution)
    if not tables:
        raise SqlError("the query reads no table of the catalog")

    checked = Checked(tree.sql(dialect=name, pretty=True), tables, cautions, _doubts(tree, tables, catalog))
    try:
        con = catalog.con
    except connection.EngineNotAvailable:
        return checked
    try:
        _explain(con, checked.sql)
    except Exception as error:  # noqa: BLE001 (whatever the engine says is what the model needs to read)
        raise SqlError(f"the engine rejected it: {_short(error)}") from error
    checked.explained = True
    return checked


def _doubts(tree: exp.Expression, tables: list[str], catalog) -> list[str]:
    """The mistake a query over statistical tables makes most: count(*) on an aggregated table counts its
    groups (months, regions), not what the table measures. Valid SQL, usually the wrong number: the model
    is asked once whether it means it."""
    from statera import classification

    if not any(isinstance(count.this, exp.Star) for count in tree.find_all(exp.Count)):
        return []
    doubts = []
    for table in tables:
        contract = catalog._contracts[table][1]
        if classification.of_table(contract).get("nature") != "aggregated":
            continue
        schema = contract["schema"][0]
        measures = [
            p["name"]
            for p in schema["properties"]
            if p.get("logicalType") in ("integer", "number") and not p.get("primaryKey")
        ]
        grain = schema.get("dataGranularityDescription") or "a group"
        doubts.append(
            f"{table} is aggregated (one row = {grain}), so count(*) counts those rows, not what the table "
            f"measures. To total a measure use sum(): its measures are {', '.join(measures) or 'in its card'}."
        )
    return doubts


def _explain(con, sql: str) -> None:
    if con.name == "duckdb":
        con.con.execute(f"EXPLAIN {sql}")
        return
    cursor = con.raw_sql(f"EXPLAIN {sql}")
    if hasattr(cursor, "close"):
        cursor.close()


def _short(error: Exception, limit: int = 600) -> str:
    text = " ".join(line.strip() for line in str(error).strip().splitlines() if line.strip())
    return text[:limit]


def run(checked: Checked, catalog) -> pd.DataFrame:
    """Runs a checked query on the engine (read-only)."""
    return connection.run_sql(catalog.con, checked.sql)


def values(request: str, catalog, limit: int = 60) -> str:
    """The most frequent values of `schema.table.column` (those containing a text, when one follows), as
    text for the model."""
    reference, _, wanted = request.strip().partition(" ")
    parts = reference.strip("`'\"").split(".")
    if len(parts) != 3:
        raise SqlError("give the column as schema.table.column (e.g. gold.sales_monthly.region)")
    table, column = ".".join(parts[:2]).lower(), parts[2]
    if table not in catalog._contracts:
        raise SqlError(f"the table {table} is not in the catalog")
    try:
        guarded = catalog.check_access(table) is not None
    except PermissionError as error:
        raise SqlError(str(error)) from error
    described = {c["name"]: c for c in catalog.describe(table).columns}
    if column not in described:
        raise SqlError(f"{table} has no column {column}; its columns: {', '.join(described)}")
    if described[column]["personal"]:
        raise SqlError(f"{table}.{column} is personal data: its values are not shown")
    quoted = exp.to_identifier(column, quoted=True).sql(dialect=dialect(catalog))
    wanted = wanted.strip().strip("`'\"")
    where = f" where lower(cast({quoted} as varchar)) like ?" if wanted else ""
    df = connection.run_sql(
        catalog.con,
        f"select {quoted} as value, count(*) as n, count(*) over () as total from {table}{where} "
        f"group by 1{f' having count(*) >= {FEW}' if guarded else ''} order by 2 desc, 1 limit {limit}",
        [f"%{wanted.lower()}%"] if wanted else None,
    )
    name = f"{table}.{column} ({described[column]['type'] or 'no type'})"
    if df.empty:
        few = f" shared by at least {FEW} rows (the table is not for broad sharing)" if guarded else ""
        found = "" if guarded or not wanted else _through_another_column(table, column, described, wanted, catalog)
        return f"{name} has no value{f' containing {wanted!r}' if wanted else ''}{few}{found}"
    shown = ", ".join(repr(v) for v in df["value"].tolist())
    total = int(df["total"].iloc[0])
    more = ""
    if total > len(df):
        more = (
            f" … INCOMPLETE: {total} distinct values, the {len(df)} most frequent shown. A value not shown may "
            f'exist: look for it with a text, e.g. "{reference} <text>"'
        )
    return f"{name} has the values{f' containing {wanted!r}' if wanted else ''}: {shown}{more}"


def _through_another_column(table: str, column: str, described: dict, wanted: str, catalog) -> str:
    """The person names a thing ("Minas Gerais") and the column holds its code ('MG'): when another text
    column of the same table has the name, the rows that match say which code goes with it."""
    name = dialect(catalog)
    quoted = exp.to_identifier(column, quoted=True).sql(dialect=name)
    for other, c in described.items():
        if other == column or c["personal"] or c["type"] != "string":
            continue
        label = exp.to_identifier(other, quoted=True).sql(dialect=name)
        df = connection.run_sql(
            catalog.con,
            f"select distinct {label} as label, {quoted} as value from {table} "
            f"where lower(cast({label} as varchar)) like ? order by 1, 2 limit 5",
            [f"%{wanted.lower()}%"],
        )
        if not df.empty:
            pairs = "; ".join(
                f"{other} = {a!r} has {column} = {b!r}" for a, b in zip(df["label"], df["value"], strict=True)
            )
            return f". But in the same table: {pairs}"
    return ""


def unknown_values(checked: Checked, catalog) -> list[str]:
    """What the query filters by that no row has: for each text compared with a column (`=`, `in`), whether
    the column holds it; when it does not, what the column holds instead. Empty when all exist (or when
    there is no engine to ask)."""
    name = dialect(catalog)
    tree = sqlglot.parse_one(checked.sql, dialect=name)
    tables = {t.alias_or_name.lower(): f"{t.db}.{t.name}".lower() for t in tree.find_all(exp.Table) if t.db}
    problems, seen = [], set()
    for node in [*tree.find_all(exp.EQ), *tree.find_all(exp.In)]:
        sides = [node.this, *(node.expressions if isinstance(node, exp.In) else [node.expression])]
        columns = [s for s in sides if isinstance(s, exp.Column)]
        texts = [s.this for s in sides if isinstance(s, exp.Literal) and s.is_string]
        if len(columns) != 1 or not texts:
            continue
        column = columns[0]
        table = (
            tables.get(column.table.lower())
            if column.table
            else (checked.tables[0] if len(checked.tables) == 1 else None)
        )
        if table not in catalog._contracts:
            continue
        described = {c["name"]: c for c in catalog.describe(table).columns}
        if column.name not in described or described[column.name]["personal"]:
            continue
        quoted = exp.to_identifier(column.name, quoted=True).sql(dialect=name)
        for text in texts:
            if (table, column.name, text) in seen:
                continue
            seen.add((table, column.name, text))
            try:
                found = connection.run_sql(catalog.con, f"select 1 from {table} where {quoted} = ? limit 1", [text])
                if found.empty:
                    known = values(f"{table}.{column.name} {text}", catalog)
                    if " has no value" in known and "But in the same table" not in known:
                        known = values(f"{table}.{column.name}", catalog, limit=30)
                    problems.append(f"No row of {table} has {column.name} = {text!r}. {known}.")
            except connection.EngineNotAvailable:
                return []
            except Exception:  # noqa: BLE001, S112 (a comparison the engine cannot make this way: nothing to say)
                continue
    return problems
