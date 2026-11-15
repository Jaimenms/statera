"""YAML mirror of the BPMN: flow conditions round-trip through standard BPMN."""

import yaml
from conftest import PROCESS

from statera.design import bpmn, processes


def test_conditions_become_conditionexpression_and_back(tmp_path):
    proc = yaml.safe_load(PROCESS)
    xml = processes.generate_xml(proc, "to-be.yaml")
    assert "${status == 'failed'}" in xml and 'xmlns:statera="urn:statera:bpmn:1.0"' in xml
    file = tmp_path / "to-be.bpmn"
    file.write_text(xml, encoding="utf-8")
    round_trip = processes.to_dict(file)
    assert round_trip["flows"] == proc["flows"]
    assert round_trip["elements"] == proc["elements"]


def test_effort_summary_ignores_exception_tasks(tmp_path):
    file = tmp_path / "to-be.bpmn"
    file.write_text(processes.generate_xml(yaml.safe_load(PROCESS), "to-be.yaml"), encoding="utf-8")
    r = bpmn.summary(bpmn.read(file))
    assert r["manual_h"] == 0.5  # Approve (30 min); Investigate is an exception


def test_flows_never_join_on_a_task(minimal_project):
    from statera import validation

    path = minimal_project.root / "products/sample/2-design/process.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("from: T_X, to: J_B", "from: T_X, to: T_B"), encoding="utf-8")  # implicit join
    findings = validation.check_processes(minimal_project, {"nodes": {}, "sources": {}, "exposures": {}})
    assert any("T_B has 2 incoming flows: join them in an exclusiveGateway" in f.message for f in findings)
    assert not any("J_B" in f.message and not f.ok for f in findings)  # a join needs no `condition`
