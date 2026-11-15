"""Evaluation alerts: shown by `statera status`, acknowledged before approving, or blocking when declared so."""

import json

import pytest

from statera import cli, execution
from statera.edition import EditionFailure

PROCESS = """
id: Evaluated_Process
name: Edition with evaluation before the review
lanes:
  - {id: L1, name: Pipeline, role: team-x}
  - {id: L2, name: Team, role: team-x}
elements:
  - {id: Start, type: startEvent, name: Start, lane: L1}
  - {id: T_Eval, type: serviceTask, name: Evaluate, lane: L1, statera: {type: automatic, gsbpm: '8.2', durationMin: 1, implementation: 'py:statera.evaluate.step'}}
  - {id: T_Review, type: userTask, name: Review, lane: L2, statera: {type: manual, gsbpm: '6.5', durationMin: 30}}
  - {id: End, type: endEvent, name: End, lane: L2}
flows:
  - {id: F1, from: Start, to: T_Eval}
  - {id: F2, from: T_Eval, to: T_Review}
  - {id: F3, from: T_Review, to: End}
"""


def _setup(project, blocking: bool = False):
    product = project.root / "products/sample"
    (product / "2-design/process.yaml").write_text(PROCESS, encoding="utf-8")
    alert = {"operator": ">=", "value": 0, **({"blocking": True} if blocking else {})}
    indicators = {
        "indicators": [
            {"id": "QI-01", "name": "Check failures", "dimension": "accuracy-and-reliability",
             "description": "Failures in the edition's checks.", "unit": "n", "alert": alert,
             "calculation": {"function": "check_failures"}},
        ]
    }  # fmt: skip
    (product / "8-evaluate").mkdir(parents=True, exist_ok=True)
    (product / "8-evaluate/indicators.yaml").write_text(json.dumps(indicators), encoding="utf-8")


def test_approving_requires_acknowledging_the_alerts(minimal_project):
    _setup(minimal_project)
    ed = execution.run("sample", "2026-01")
    assert (ed.status, ed.pending) == ("waiting", "T_Review")
    assert [a["id"] for a in ed.alerts] == ["QI-01"]
    with pytest.raises(EditionFailure, match="acknowledge-alerts"):
        execution.resume("sample", "2026-01")
    assert execution.state("sample", "2026-01").status == "waiting"  # nothing recorded
    ed = execution.resume("sample", "2026-01", acknowledgement="known delay in the source, see AP-01")
    assert ed.status == "done"
    assert ed.acknowledgement["note"] == "known delay in the source, see AP-01"
    assert ed.acknowledgement["task"] == "T_Review" and ed.acknowledgement["alerts"] == ["QI-01"]
    folder = minimal_project.product("sample").data(8) / "2026-01"
    evaluation = json.loads((folder / "evaluation.json").read_text(encoding="utf-8"))
    assert evaluation["acknowledgement"]["note"].startswith("known delay")  # published with the edition
    assert "**Acknowledged** by" in (folder / "evaluation.md").read_text(encoding="utf-8")


def test_rejecting_needs_no_acknowledgement(minimal_project):
    _setup(minimal_project)
    execution.run("sample", "2026-01")
    ed = execution.resume("sample", "2026-01", decision="rejected")
    assert ed.acknowledgement is None


def test_the_demo_acknowledges_as_simulated(minimal_project):
    _setup(minimal_project)
    ed = execution.run("sample", "2026-01", approve=True)
    assert ed.status == "done" and ed.acknowledgement["simulated"] is True


def test_blocking_alert_stops_the_edition(minimal_project):
    _setup(minimal_project, blocking=True)
    ed = execution.run("sample", "2026-01", approve=True)
    assert (ed.status, ed.pending) == ("blocked", "T_Eval")  # no exception path modeled: blocked
    assert any("QI-01" in f and "blocking" in f for f in ed.failures)


def test_status_shows_the_alerts_and_the_acknowledgement(minimal_project, capsys):
    _setup(minimal_project)
    execution.run("sample", "2026-01")
    assert cli.main(["status", "--product", "sample", "--period", "2026-01"]) == 0
    out = capsys.readouterr().out
    assert "1 indicator(s) in alert" in out and "QI-01 Check failures" in out and "not acknowledged" in out
    task = execution.tasks()[0]  # `statera tasks` gives the command with the acknowledgement it needs
    assert task["command"] == 'statera resume --product sample --period 2026-01 --acknowledge-alerts "<why the edition goes on>"'  # fmt: skip
    assert "acknowledging the 1 alert(s)" in task["does"]
    execution.resume("sample", "2026-01", acknowledgement="seen")
    cli.main(["status", "--product", "sample", "--period", "2026-01"])
    assert "acknowledged by" in capsys.readouterr().out
