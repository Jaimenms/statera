"""Fake task implementations, used by the test project's process."""

import os

CALLS: list[tuple[str, str]] = []


def record(ed) -> str:
    CALLS.append(("A", ed.period))
    return "ok"


def decide(ed) -> str:
    return os.environ.get("STEP_DECISION", "ok")


def publish(ed) -> str:
    CALLS.append(("P", ed.period))
    return "ok"


def extract(ed) -> str:
    CALLS.append(("E", ed.cut_date))
    return "ok"


def mark_cut(ed) -> str:
    """Minimal version of statera.disseminate.mark_cut: writes the cut's immutable manifest."""
    import json

    from statera.collect import cut_file

    destination = cut_file(ed.unit, ed.cut_date)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"product": ed.product, "cutDate": ed.cut_date, "run": ed.run_id}))
    return "ok"
