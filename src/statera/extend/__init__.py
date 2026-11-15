"""statera.extend — the public extension API (specification 05, "Extension classes").

Every step of a process, the framework's and the products', is a class that extends one of four abstractions:

    Task             run(ctx: TaskContext) -> TaskResult          <phase>/tasks.py
    Check            evaluate(ctx: CheckContext) -> CheckResult   6-analyse/checks.py (run by a Gate)
    Await            ready(ctx: TaskContext) -> bool              <phase>/tasks.py
    SyntheticSource  generate(ctx, fault) -> dict[str, int]       3-build/synthetic.py

The process names a class (`implementation: Deduplicate`, `awaits: PartnerFileArrived`), resolved in the
product first, then in the framework (Gate, DbtBuild, Extract, Package, Publish, MarkCut, Evaluate, PinCut;
checks RevisionBetweenEditions and Outlier). From 0.6.0 this API follows semantic versioning: a name is removed
or a signature changes only after one minor version in which the old form still works and warns.
"""

from statera.extend.base import (
    Await,
    Check,
    CheckContext,
    CheckResult,
    SyntheticContext,
    SyntheticSource,
    Task,
    TaskContext,
    TaskResult,
    UndeclaredTable,
)
from statera.extend.framework import (
    DbtBuild,
    Evaluate,
    Extract,
    Gate,
    MarkCut,
    Outlier,
    Package,
    PinCut,
    Publish,
    RevisionBetweenEditions,
)

__all__ = [
    "Await", "Check", "CheckContext", "CheckResult", "SyntheticContext", "SyntheticSource", "Task", "TaskContext",
    "TaskResult", "UndeclaredTable",
    "DbtBuild", "Evaluate", "Extract", "Gate", "MarkCut", "Outlier", "Package", "PinCut", "Publish",
    "RevisionBetweenEditions",
]  # fmt: skip
