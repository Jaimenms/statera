"""An edition being run: the context shared by the process tasks.

The state is written to `<product>/8-evaluate/data/runs/<period>.state.json` after each
task, so the process can stop at a human task and resume later (`statera resume`), even on
another machine or another day.

Every automatic task is a function `step(ed: Edition) -> str` that returns the STATUS
("ok" or "failed"); the status decides the path at the process gateways.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from statera.project import Project, Unit, current


class EditionFailure(RuntimeError):
    """A task could not be completed (e.g. edition already published, classification gate)."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Edition:
    product: str
    period: str
    cut_date: str
    force: bool = False
    status: str = "running"  # running | waiting | blocked | done
    pending: str | None = None  # id of the human task awaiting completion
    run_id: str = ""  # OpenLineage run of the edition (parent of the task runs)
    extractions: list[dict] = field(default_factory=list)
    dbt_results: dict = field(default_factory=dict)  # tests, failures, nodes executed
    checks: int = 0  # contract checks executed
    non_blocking_failures: int = 0
    failures: list[str] = field(default_factory=list)  # blocking failures (dbt, contracts, controls)
    revisions: list[dict] | None = None  # result of the revision control between editions
    publication: dict = field(default_factory=dict)  # folder, version, correction, suppressions
    cuts: dict = field(default_factory=dict)  # manifests of the pinned cuts of the input internal products
    history: list[dict] = field(default_factory=list)  # completed tasks, with status
    ctx: dict = field(default_factory=dict, repr=False)  # OpenLineage context of the current task

    # --- project access ---------------------------------------------------------------------
    @property
    def project(self) -> Project:
        return current()

    @property
    def unit(self) -> Unit:
        return self.project.product(self.product)

    @property
    def label(self) -> str:
        """'Edition foo 2026-05' or, for an internal product, 'Cut base-data 2026-07-20'."""
        return f"Cut {self.product} {self.cut_date}" if self.unit.internal else f"Edition {self.product} {self.period}"

    @property
    def facet(self) -> dict:
        return {"product": self.product, "period": self.period, "cutDate": self.cut_date}

    @property
    def parent(self) -> dict:
        return {"run_id": self.run_id, "job": f"{self.product}.edition"}

    @property
    def events_file(self) -> Path:
        return self.unit.runs_folder / "events.jsonl"

    def data_folder(self, phase: int) -> Path:
        """Edition folder inside the phase data: <product>/<phase>/data/<period>/."""
        return self.unit.data(phase) / self.period

    # --- logging ----------------------------------------------------------------------------
    def log(self, message: str) -> None:
        print(f"[statera] {message}", flush=True)

    def record_task(self, task: str, status: str, duration_s: float | None = None, **extra) -> None:
        """One line per task in runs.jsonl (input to the duration indicator, phase 8)."""
        line = {
            **self.facet,
            "bpmn_task": task,
            "run_id": self.ctx.get("run_id", ""),
            "duration_s": duration_s,
            "status": status,
            "registered_at": now(),
            **extra,
        }
        self.history.append({"task": task, "status": status, **extra})
        destination = self.unit.runs_folder / "runs.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # --- persistence ------------------------------------------------------------------------
    @staticmethod
    def state_file(unit: Unit, period: str) -> Path:
        return unit.runs_folder / f"{period}.state.json"

    def save(self) -> Path:
        destination = self.state_file(self.unit, self.period)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k != "ctx"}
        destination.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return destination

    @classmethod
    def load(cls, unit: Unit, period: str) -> Edition:
        path = cls.state_file(unit, period)
        if not path.exists():
            raise EditionFailure(f"No run of {unit.name} {period} to resume ({path})")
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
