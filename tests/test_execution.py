"""The product's process is the run plan: executor semantics."""

import pytest
import testing_steps

from statera import execution
from statera.edition import read_jsonl


@pytest.fixture(autouse=True)
def clear_calls():
    testing_steps.CALLS.clear()


def test_stops_at_the_human_task_and_resumes(minimal_project):
    ed = execution.run("sample", "2026-01")
    assert (ed.status, ed.pending) == ("waiting", "T_H")
    assert testing_steps.CALLS == [("A", "2026-01")]
    ed = execution.resume("sample", "2026-01")  # approves -> stops at the semi-automatic task
    assert (ed.status, ed.pending) == ("waiting", "T_P")
    ed = execution.resume("sample", "2026-01")  # completes the publication (runs the implementation)
    assert ed.status == "done"
    assert testing_steps.CALLS[-1] == ("P", "2026-01")
    assert [h["task"] for h in ed.history] == ["T_A", "T_B", "T_H", "T_P"]


def test_rejection_goes_back_through_the_modeled_flow(minimal_project):
    execution.run("sample", "2026-01")
    ed = execution.resume("sample", "2026-01", decision="rejected")
    assert (ed.status, ed.pending) == ("waiting", "T_H")


def test_failure_follows_the_exception_path(minimal_project, monkeypatch):
    monkeypatch.setenv("STEP_DECISION", "failed")
    ed = execution.run("sample", "2026-01", approve=True)
    assert (ed.status, ed.pending) == ("blocked", "T_X")


def test_approve_simulates_the_human_tasks(minimal_project):
    ed = execution.run("sample", "2026-01", approve=True)
    assert ed.status == "done"
    assert any(h.get("simulated") for h in ed.history)


def test_records_duration_and_valid_openlineage_events(minimal_project):
    from statera import openlineage

    ed = execution.run("sample", "2026-01", approve=True)
    folder = minimal_project.product("sample").runs_folder
    lines = read_jsonl(folder / "runs.jsonl")
    assert {line["bpmn_task"] for line in lines} >= {"T_A", "T_B"}
    events = read_jsonl(folder / "events.jsonl")
    assert all(openlineage.validate(ev) == [] for ev in events)
    assert {ev["eventType"] for ev in events if ev["job"]["name"] == "sample.edition"} == {"START", "COMPLETE"}
    assert events[0]["job"]["namespace"] == "org.example"
    assert ed.run_id


def test_consumer_awaits_the_cut_and_resumes_when_released(minimal_project):
    ed = execution.run("consumer", "2026-01")
    assert (ed.status, ed.pending) == ("waiting", "Ev_Cut")
    assert testing_steps.CALLS == []
    ed = execution.resume("consumer", "2026-01")  # cut not released yet: keeps waiting
    assert (ed.status, ed.pending) == ("waiting", "Ev_Cut")
    cut = execution.run("base-data", cut_date="2026-02-20")
    assert cut.status == "done"
    ed = execution.resume("consumer", "2026-01")
    assert ed.status == "done"
    assert ed.cuts["base-data"]["run"] == cut.run_id  # the edition pins the cut it read
    assert [h["task"] for h in ed.history] == ["Ev_Cut", "T_Compute"]


def test_internal_product_requires_cut_date(minimal_project):
    with pytest.raises(execution.EditionFailure, match="cut-date"):
        execution.run("base-data")


def test_with_dependencies_releases_the_cut_and_reuses_it(minimal_project):
    ed = execution.run("consumer", "2026-01", with_dependencies=True)
    assert ed.status == "done"
    ed = execution.run("consumer", "2026-02", with_dependencies=True)  # same cut-off date
    assert ed.status == "done"
    extractions = [c for c in testing_steps.CALLS if c[0] == "E"]
    assert extractions == [("E", "2026-02-20")]  # a single cut for both editions
