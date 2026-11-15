"""Phase 8 — Evaluate. Process task: `implementation: py:statera.evaluate.step`."""

from __future__ import annotations

from statera.edition import Edition
from statera.evaluate import evaluation


def step(ed: Edition) -> str:
    """GSBPM 8.1 and 8.2 — quality indicators of the edition.

    An alert does not stop the edition: the next human task can only approve it after someone
    acknowledges the alerts (`statera resume --acknowledge-alerts "<note>"`). An indicator whose alert
    is `blocking: true` makes the task fail: the edition follows the exception path modeled after the
    task, or is blocked."""
    report = evaluation.evaluate(ed)
    ed.log(f"  evaluation: {ed.project.rel(report)} — {len(ed.alerts)} indicator(s) in alert")
    for a in ed.alerts:
        blocking = " — BLOCKING" if a["blocking"] else ""
        ed.log(f"    ⚠ {a['id']} {a['name']}: {a['formatted_value']} (alert {a['alert_text']}){blocking}")
    blocking = [a for a in ed.alerts if a["blocking"]]
    ed.failures += [
        f"{a['id']} {a['name']}: {a['formatted_value']} (blocking alert {a['alert_text']})" for a in blocking
    ]
    return "failed" if blocking else "ok"
