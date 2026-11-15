"""Product skeleton: the same files in every product, and the generated calendar of internal products."""

import yaml

from statera import generated, release_calendar, validation
from statera.project import current


def test_internal_product_calendar_comes_from_consumers(minimal_project):
    p = current()
    requested_cuts = release_calendar.requested_cuts(p, p.product("base-data"))
    assert requested_cuts == {"2026-02-20": ["consumer 2026-01", "consumer 2026-02"]}  # one cut, two editions
    text = release_calendar.generate(p, p.product("base-data"))
    assert text.startswith(f"# {generated.MARKER}") and yaml.safe_load(text)["cuts"][0]["cut_date"] == "2026-02-20"


def test_not_applicable_counts_as_present_but_without_content(minimal_project):
    p = current()
    u = p.product("base-data")
    file = u.folder / "7-disseminate" / "series.yaml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('not_applicable: "internal product: not disseminated"\n', encoding="utf-8")
    assert u.read("7-disseminate/series.yaml") is None and not u.has("7-disseminate/series.yaml")
    assert u.not_applicable("7-disseminate/series.yaml") == "internal product: not disseminated"


def test_incomplete_skeleton_is_reported(minimal_project):
    messages = [a.message for a in validation.check_skeleton(current()) if not a.ok]
    assert any("base-data: missing 6-analyse/controls.yaml" in m for m in messages)
