"""The four abstractions every process step extends, with their contexts and results (specification 05).

    Task             serviceTask, or the automatic part of a semi-automatic userTask   run(ctx) -> TaskResult
    Check            runs inside a Gate (a Task)                                       evaluate(ctx) -> CheckResult
    Await            intermediateCatchEvent                                            ready(ctx) -> bool
    SyntheticSource  feeds tests and demos                                             generate(ctx, fault) -> dict

A class DECLARES what it does (GSBPM sub-process, tables read and written, possible outcomes); validation
checks those declarations against the process and the contracts without running anything.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import pandas as pd

    from statera.edition import Edition
    from statera.project import Project, Unit

DEFAULT_OUTCOMES = frozenset({"ok", "failed"})
VERDICTS = ("ok", "warning", "failed")


class UndeclaredTable(PermissionError):
    """A task read or wrote a table it does not declare: lineage would be wrong."""


def gsbpm_codes(text: str | None) -> set[str]:
    """'5.1-5.3;6.4' -> {'5.1', '5.2', '5.3', '6.4'} (sub-process codes, ranges expanded)."""
    codes: set[str] = set()
    for part in re.split(r"[;,\s]+", text or ""):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            phase, first = start.split(".")
            last = end.split(".")[-1]
            codes.update(f"{phase}.{n}" for n in range(int(first), int(last) + 1))
        else:
            codes.add(part)
    return codes


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    """What a task returns: the outcome (one of the task's `outcomes`), a message and metrics, recorded in
    runs.jsonl. The outcome decides the path at the gateway after the task."""

    outcome: str
    message: str = ""
    metrics: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, message: str = "", **metrics) -> TaskResult:
        return cls("ok", message, metrics)

    @classmethod
    def failed(cls, message: str = "", **metrics) -> TaskResult:
        return cls("failed", message, metrics)

    @classmethod
    def of(cls, outcome: str, message: str = "", **metrics) -> TaskResult:
        return cls(outcome, message, metrics)


class TaskContext:
    """What a task (or an await) sees of the run. Read-only about the run; `read` and `write` only accept the
    tables the class declares, so the OpenLineage inputs and outputs are right by construction."""

    def __init__(self, edition: Edition, element: dict | None, inputs=(), outputs=(), phase: int | None = None):
        self._edition = edition
        self._element = element or {}
        ext = self._element.get("statera") or {}
        self.product: str = edition.product
        self.period: str = edition.period
        self.cut_date: str = edition.cut_date
        self.run_id: str = edition.run_id
        self.params: Mapping = MappingProxyType(dict(ext.get("params") or {}))
        self._inputs, self._outputs = set(inputs), set(outputs)
        codes = sorted(gsbpm_codes(ext.get("gsbpm")))
        self.phase: int = phase or (int(codes[0].split(".")[0]) if codes else 5)
        self.folder: Path = edition.data_folder(self.phase)

    def _connect(self, read_only: bool):
        import duckdb

        return duckdb.connect(str(self._edition.project.warehouse), read_only=read_only)

    def read(self, table: str) -> pd.DataFrame:
        """A declared input (or output) table, from the engine, as a DataFrame."""
        if table not in self._inputs | self._outputs:
            raise UndeclaredTable(f"{table} is not among the task's declared inputs: add it to `inputs`")
        con = self._connect(read_only=True)
        try:
            return con.execute(f"select * from {table}").df()
        finally:
            con.close()

    def write(self, table: str, df: pd.DataFrame) -> None:
        """Replaces a declared output table in the engine (idempotent for the same run)."""
        if table not in self._outputs:
            raise UndeclaredTable(f"{table} is not among the task's declared outputs: add it to `outputs`")
        schema = table.split(".")[0] if "." in table else "main"
        con = self._connect(read_only=False)
        try:
            con.register("statera_output", df)
            con.execute(f"create schema if not exists {schema}")
            con.execute(f"create or replace table {table} as select * from statera_output")
        finally:
            con.close()

    def log(self, message: str) -> None:
        self._edition.log(f"  {message}")


class Task:
    """A step of the process: a serviceTask, or the automatic part of a semi-automatic userTask.

    Declares `gsbpm` (sub-process, checked against the process), `inputs` and `outputs` (contract tables, by
    physical name) and `outcomes` (what the gateway after it may test). `run` must be idempotent for the same
    product, period and cut-off date. A raised exception is the outcome `failed`, with its message."""

    gsbpm: ClassVar[str | None] = None
    inputs: ClassVar[list[str]] = []
    outputs: ClassVar[list[str]] = []
    outcomes: ClassVar[frozenset[str] | set[str]] = DEFAULT_OUTCOMES

    @classmethod
    def tables(cls, unit: Unit) -> tuple[list[str], list[str]]:
        """(inputs, outputs) of the task in a product. Framework tasks override it to derive them."""
        return list(cls.inputs), list(cls.outputs)

    def run(self, ctx: TaskContext) -> TaskResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """A check's verdict: `ok`, `warning` or `failed`, with a message and, optionally, the rows behind it."""

    verdict: str
    message: str = ""
    rows: list[dict] | None = None
    table: list[dict] | None = None  # a table the gate keeps for the summary (e.g. the revisions)

    @classmethod
    def ok(cls, message: str = "", rows=None, table=None) -> CheckResult:
        return cls("ok", message, _records(rows), table)

    @classmethod
    def warn(cls, message: str, rows=None, table=None) -> CheckResult:
        return cls("warning", message, _records(rows), table)

    @classmethod
    def fail(cls, message: str, rows=None, table=None) -> CheckResult:
        return cls("failed", message, _records(rows), table)


def _records(rows) -> list[dict] | None:
    if rows is None:
        return None
    if hasattr(rows, "to_dict"):
        return rows.to_dict("records")
    return list(rows)


class CheckContext:
    """What a check sees: the edition's period, the control's parameters (its entry in controls.yaml) and the
    tables of the product's ports, in this edition and in the previous published one."""

    def __init__(
        self,
        product: str,
        period: str,
        params: Mapping,
        current: Callable[[str], pd.DataFrame],
        previous: Callable[[str], pd.DataFrame | None],
        period_column: str = "period",
        log: Callable[[str], None] = print,
    ):
        self.product, self.period, self.period_column = product, period, period_column
        self.params: Mapping = MappingProxyType(dict(params))
        self._current, self._previous, self._log = current, previous, log

    def current(self, port: str) -> pd.DataFrame:
        return self._current(port)

    def previous(self, port: str) -> pd.DataFrame | None:
        """The same port in the previous published edition; None when there is none."""
        return self._previous(port)

    def log(self, message: str) -> None:
        self._log(message)


class Check:
    """A control of the edition, run by a Gate. `severity = "error"` blocks the edition when the check fails;
    `"warning"` records it (a control in controls.yaml may override it with `severity`). A product check
    must have cases in `6-analyse/check-cases/<Check>.yaml`."""

    severity: ClassVar[str] = "error"
    gsbpm: ClassVar[str | None] = "6.2"

    def evaluate(self, ctx: CheckContext) -> CheckResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Await and SyntheticSource
# ---------------------------------------------------------------------------


class Await:
    """An intermediateCatchEvent: while `ready` returns False, the edition stays waiting on it, and
    `statera resume` re-evaluates."""

    def ready(self, ctx: TaskContext) -> bool:
        raise NotImplementedError


class SyntheticContext:
    """What a synthetic source sees: the project, its product and where the extraction reads the sources."""

    def __init__(self, project: Project, unit: Unit, references: bool = False, log: Callable[[str], None] = print):
        self.project, self.unit, self.product = project, unit, unit.name
        self.references = references  # also regenerate the synthetic reference tables
        self.source_folder: Path = unit.data(4) / "source"
        self._log = log

    def log(self, message: str) -> None:
        self._log(message)


class SyntheticSource:
    """Stands in for a source system in tests and demos. Declares the bronze contracts it stands in for
    (`sources`) and the faults it can inject to exercise the checks (`faults`)."""

    sources: ClassVar[list[str]] = []
    faults: ClassVar[set[str] | frozenset[str]] = frozenset()

    def generate(self, ctx: SyntheticContext, fault: str | None = None) -> dict[str, int]:
        raise NotImplementedError


ABSTRACTIONS = (Task, Check, Await, SyntheticSource)
