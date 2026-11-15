"""Lineage events in the OpenLineage standard (Job, Run, Dataset).

Each automatic task of an edition emits START and COMPLETE (or FAIL) events, written to
`<product>/8-evaluate/data/runs/events.jsonl`. Events are validated against the official
JSON Schema and can be sent to any compatible server (Marquez, OpenMetadata, DataHub).

The custom `statera_edition` facet records product, reference month and cut-off date on each run:
it is what answers "which extraction did this number come from". The jobs' namespace is the
organization's namespace (e.g. org.example.labor-stats), declared in statera.toml.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from statera import __version__
from statera.project import STANDARD_SCHEMAS, current

SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
EDITION_FACET_URL = "https://example.org/statera/facets/StateraEditionRunFacet.json"
PRODUCER = f"https://example.org/statera/v{__version__}"


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((STANDARD_SCHEMAS / "openlineage-2-0-2.json").read_text()))


def new_run_id() -> str:
    return str(uuid.uuid4())


def dataset(name: str, namespace: str | None = None) -> dict:
    return {"namespace": namespace or current().data_namespace, "name": name}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event(kind, job, run_id, inputs, outputs, edition: dict, parent: dict | None = None) -> dict:
    namespace = current().openlineage_namespace
    run_facets = {"statera_edition": {"_producer": PRODUCER, "_schemaURL": EDITION_FACET_URL, **edition}}
    if parent:
        run_facets["parent"] = {
            "_producer": PRODUCER,
            "_schemaURL": "https://openlineage.io/spec/facets/1-0-1/ParentRunFacet.json",
            "run": {"runId": parent["run_id"]},
            "job": {"namespace": namespace, "name": parent["job"]},
        }
    return {
        "eventType": kind,
        "eventTime": _now(),
        "producer": PRODUCER,
        "schemaURL": SCHEMA_URL,
        "run": {"runId": run_id, "facets": run_facets},
        "job": {"namespace": namespace, "name": job},
        "inputs": inputs,
        "outputs": outputs,
    }


def validate(ev: dict) -> list[str]:
    return [e.message for e in _validator().iter_errors(ev)]


def emit(ev: dict, file: Path) -> None:
    errors = validate(ev)
    if errors:
        raise ValueError(f"Invalid OpenLineage event: {errors}")
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


@contextmanager
def execution(job, inputs, outputs, edition: dict, file: Path, parent: dict | None = None):
    """Emits START on enter and COMPLETE/FAIL on exit. Yields the run context."""
    run_id = new_run_id()
    start = datetime.now(UTC)
    emit(event("START", job, run_id, inputs, outputs, edition, parent), file)
    context = {"run_id": run_id, "job": job, "inputs": inputs, "outputs": outputs}
    try:
        yield context
    except BaseException:
        emit(event("FAIL", job, run_id, context["inputs"], context["outputs"], edition, parent), file)
        raise
    else:
        emit(event("COMPLETE", job, run_id, context["inputs"], context["outputs"], edition, parent), file)
    finally:
        context["duration_s"] = round((datetime.now(UTC) - start).total_seconds(), 2)


def read_events(file: Path) -> list[dict]:
    if not file.exists():
        return []
    lines = file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
