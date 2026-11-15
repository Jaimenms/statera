"""Framework tests: independent of any real content repository.

The minimal project below has a product with a process that exercises the executor:
automatic task, gateway with conditions, exception path, human task, rejection and
semi-automatic task. And a small mesh of products: the internal source product `base-data`
releases cuts, and the dissemination product `consumer` waits for the cut in an intermediate event.
"""

import textwrap

import pytest

PROCESS = """
id: Sample_Process
name: Sample process
lanes:
  - {id: L1, name: Pipeline, role: team-x}
  - {id: L2, name: Team, role: team-x}
elements:
  - {id: Start, type: startEvent, name: Start, lane: L1}
  - {id: T_A, type: serviceTask, name: A, lane: L1, statera: {type: automatic, gsbpm: '4.3', durationMin: 1, implementation: 'py:testing_steps.record'}}
  - {id: J_B, type: exclusiveGateway, lane: L1}
  - {id: T_B, type: serviceTask, name: B, lane: L1, statera: {type: automatic, gsbpm: '6.2', durationMin: 1, implementation: 'py:testing_steps.decide'}}
  - {id: G, type: exclusiveGateway, name: 'OK?', lane: L1}
  - {id: T_X, type: userTask, name: Investigate, lane: L2, statera: {type: manual, gsbpm: '5.3', durationMin: 60, exception: true}}
  - {id: J_H, type: exclusiveGateway, lane: L2}
  - {id: T_H, type: userTask, name: Approve, lane: L2, statera: {type: manual, gsbpm: '6.5', durationMin: 30}}
  - {id: G2, type: exclusiveGateway, name: 'Approved?', lane: L2}
  - {id: T_P, type: userTask, name: Publish, lane: L2, statera: {type: semi-automatic, gsbpm: '7.3', durationMin: 10, implementation: 'py:testing_steps.publish'}}
  - {id: End, type: endEvent, name: End, lane: L2}
flows:
  - {id: F1, from: Start, to: T_A}
  - {id: F2, from: T_A, to: J_B}
  - {id: F2b, from: J_B, to: T_B}
  - {id: F3, from: T_B, to: G}
  - {id: F4, from: G, to: J_H, name: 'yes', condition: ok}
  - {id: F4b, from: J_H, to: T_H}
  - {id: F5, from: G, to: T_X, name: 'no', condition: failed}
  - {id: F6, from: T_X, to: J_B}
  - {id: F7, from: T_H, to: G2}
  - {id: F8, from: G2, to: T_P, name: 'yes', condition: approved}
  - {id: F9, from: G2, to: J_H, name: 'no', condition: rejected}
  - {id: F10, from: T_P, to: End}
"""

# Internal source product: extracts (fake) and marks the cut (fake: only writes the manifest).
SOURCE_PROCESS = """
id: Source_Process
name: Base data cut
lanes:
  - {id: L1, name: Pipeline, role: team-x}
elements:
  - {id: Start, type: startEvent, name: Cut-off date, lane: L1, event: timer}
  - {id: T_Extract, type: serviceTask, name: Extract, lane: L1, statera: {type: automatic, gsbpm: '4.3', durationMin: 1, implementation: 'py:testing_steps.extract'}}
  - {id: T_Mark, type: serviceTask, name: Mark the cut, lane: L1, statera: {type: automatic, gsbpm: '7.3', durationMin: 1, implementation: 'py:testing_steps.mark_cut'}}
  - {id: End, type: endEvent, name: Cut available, lane: L1}
flows:
  - {id: F1, from: Start, to: T_Extract}
  - {id: F2, from: T_Extract, to: T_Mark}
  - {id: F3, from: T_Mark, to: End}
"""

# Consumer: waits for the internal product's cut (message event) and computes.
CONSUMER_PROCESS = """
id: Consumer_Process
name: Consumer edition
lanes:
  - {id: L1, name: Pipeline, role: team-x}
elements:
  - {id: Start, type: startEvent, name: Start, lane: L1}
  - {id: Ev_Cut, type: intermediateCatchEvent, name: Cut available, lane: L1, event: message, statera: {awaits: 'py:statera.collect.pin_cut'}}
  - {id: T_Compute, type: serviceTask, name: Compute, lane: L1, statera: {type: automatic, gsbpm: '5.1', durationMin: 1, implementation: 'py:testing_steps.record'}}
  - {id: End, type: endEvent, name: End, lane: L1}
flows:
  - {id: F1, from: Start, to: Ev_Cut}
  - {id: F2, from: Ev_Cut, to: T_Compute}
  - {id: F3, from: T_Compute, to: End}
"""


@pytest.fixture
def minimal_project(tmp_path, monkeypatch):
    root = tmp_path / "organization"
    files = {
        "statera.toml": """
            [project]
            name = "example-org"
            organization = "Example organization"
            acronym = "ex"
            namespace = "org.example"
        """,
        "governance/roles.yaml": """
            roles:
              - {id: team-x, name: Team X, org_unit: "000001", git_team: "@org/team-x"}
        """,
        "products/sample/1-specify/product.odps.yaml": """
            apiVersion: v1.0.0
            kind: DataProduct
            id: sample
            name: Sample product
            status: active
            customProperties:
              - {property: urn, value: org.example.sample}
        """,
        "products/sample/1-specify/calendar.yaml": """
            product: sample
            frequency: monthly
            editions:
              - {period: "2026-01", cut_date: "2026-02-20", release: "2026-02-27"}
        """,
        "products/base-data/1-specify/product.odps.yaml": """
            apiVersion: v1.0.0
            kind: DataProduct
            id: base-data
            name: Base data (internal source product)
            status: active
            customProperties:
              - {property: urn, value: org.example.base-data}
              - {property: alignment, value: source}
              - {property: visibility, value: internal}
              - property: authorizedConsumers
                value:
                  - {product: consumer, purpose: test}
                  - {role: team-x, purpose: study, until: "2099-12-31", act: Ordinance 1}
            outputPorts:
              - {name: records, version: 1.0.0, contractId: bronze-records}
        """,
        "products/base-data/4-collect/contracts/records.odcs.yaml": """
            apiVersion: v3.1.0
            kind: DataContract
            id: bronze-records
            name: Records (bronze)
            version: 1.0.0
            status: active
            customProperties:
              - {property: layer, value: bronze}
            schema:
              - name: records
                physicalName: bronze.records
                properties:
                  - {name: id, logicalType: string}
        """,
        "products/base-data/2-design/process.yaml": SOURCE_PROCESS,
        "products/consumer/1-specify/product.odps.yaml": """
            apiVersion: v1.0.0
            kind: DataProduct
            id: consumer
            name: Consumer of the base data
            status: active
            customProperties:
              - {property: urn, value: org.example.consumer}
            inputPorts:
              - {name: records, version: 1.0.0, contractId: bronze-records}
        """,
        "products/consumer/1-specify/calendar.yaml": """
            product: consumer
            frequency: monthly
            editions:
              - {period: "2026-01", cut_date: "2026-02-20", release: "2026-02-27"}
              - {period: "2026-02", cut_date: "2026-02-20", release: "2026-03-27"}
        """,
        "products/consumer/2-design/process.yaml": CONSUMER_PROCESS,
        "products/sample/2-design/process.yaml": PROCESS,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    monkeypatch.setenv("STATERA_PROJECT", str(root))
    monkeypatch.delenv("STATERA_DATA", raising=False)
    monkeypatch.delenv("STATERA_REGISTRY", raising=False)
    monkeypatch.chdir(root)
    from statera import project

    return project.current()
