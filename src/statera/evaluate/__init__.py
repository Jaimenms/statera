"""Phase 8 — Evaluate. Process task: `implementation: py:statera.evaluate.step`."""

from __future__ import annotations

from statera.edition import Edition
from statera.evaluate import evaluation


def step(ed: Edition) -> str:
    """GSBPM 8.1 and 8.2 — quality indicators of the edition."""
    report = evaluation.evaluate(ed)
    ed.log(f"  evaluation: {ed.project.rel(report)}")
    return "ok"
