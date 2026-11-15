# 05 — Processes and execution

## Format

The process is a YAML file mirroring BPMN 2.0 one-to-one (schema `process`). Element types use the
names from the OMG specification (`startEvent`, `serviceTask`, `userTask`, `exclusiveGateway`,
`dataStoreReference`…); what belongs to the standard goes in the `statera` block of each task:

| Attribute | Values |
|---|---|
| `type` | `automatic` · `semi-automatic` · `manual` |
| `gsbpm` | Sub-process(es): `4.3`, `5.1-5.7`, `6.4;7.2` |
| `durationMin` | Expected duration per edition (minutes) |
| `implementation` | `py:<module>.<function>` or `dbt:build` |
| `models` | With `dbt:build`: models to build, with everything upstream of them |
| `exception` | `true` for tasks off the normal path |
| `awaits` | On an `intermediateCatchEvent`: `py:<module>.<function>` that says whether the wait is over (e.g. the cut released, 09) |

Flows leaving gateways **must** have a `condition` (in an executable process): the status of the
last task that makes the executor follow that flow. In the generated BPMN it becomes
`<conditionExpression xsi:type="tFormalExpression">${status == 'ok'}</conditionExpression>`, which
comes back to the YAML on import. Data stores may have an `asset` (contract id), used for the
task's lineage datasets.

## Execution

The product's to-be process (`2-design/processes/to-be.yaml`) is the execution plan:

| Element | The executor |
|---|---|
| `startEvent` | Starts the edition (OpenLineage run of the edition) |
| Automatic task | Runs the implementation, which returns the status (`ok` or `failed`); records duration and events |
| `exclusiveGateway` | Follows the flow whose `condition` is the status of the last task |
| Human task (manual or semi-automatic) | Stops and saves the state (`waiting`); `statera resume` completes it with `approved` (or `rejected`, with `--reject`); a semi-automatic task runs its implementation when completed |
| Intermediate event with `awaits` | Calls the function; if it is not yet time, the edition stays `waiting` on it, and `statera resume` re-evaluates |
| Task with `exception: true` | The edition becomes `blocked`, with the failures recorded |
| `endEvent` | The edition is `done` |

An automatic task that fails with no gateway after it also blocks the edition. Framework
implementations: `py:statera.collect.step`, `dbt:build`, `py:statera.analyse.step`,
`py:statera.disseminate.step`, `py:statera.evaluate.step` and `py:statera.disseminate.publish`.
In an internal product, the run is a **cut** (`--cut-date` is given, not the reference month), and
its own implementations are `py:statera.disseminate.mark_cut` (task) and
`py:statera.collect.pin_cut` (the consumer's wait), 09. With `--with-dependencies`, the run first
releases the missing cuts in the input internal products and reuses those already released.
Every implementation receives the edition context and returns the status; an organization can register
its own.

The state is kept in `<product>/8-evaluate/data/runs/<reference-month>.state.json`; the task
log in `runs.jsonl`; the OpenLineage events in `events.jsonl`, all in the same folder.
