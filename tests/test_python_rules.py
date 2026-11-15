"""Rule cases in Python models: run by the framework, calling transform()."""

import textwrap

from statera.process import rules
from statera.project import current

MODEL = textwrap.dedent(
    """
    def transform(inputs, variables):
        df = inputs["int_sample__base"].copy()
        df["double"] = df["value"] * 2
        return df

    def model(dbt, session):
        return transform({"int_sample__base": dbt.ref("int_sample__base").df()}, {})
    """
)


def _rule(root, expected, requires=""):
    folder = root / "products/sample/5-process/rules/BR-T-001-double"
    folder.mkdir(parents=True)
    (folder / "rule.md").write_text("# BR-T-001 — Double\n", encoding="utf-8")
    (folder / "cases.yaml").write_text(
        f"rule: BR-T-001\nmodel: sample_double\n{requires}cases:\n"
        f"  - id: doubles\n    description: doubles the value\n"
        f"    inputs: {{int_sample__base: [{{value: 2}}, {{value: 5}}]}}\n"
        f"    expected: {expected}\n",
        encoding="utf-8",
    )
    models = root / "products/sample/5-process/models/marts/sample"
    models.mkdir(parents=True)
    (models / "sample_double.py").write_text(MODEL, encoding="utf-8")


def test_python_case_passes_and_does_not_become_dbt_unit_test(minimal_project):
    _rule(minimal_project.root, "[{value: 5, double: 10}, {value: 2, double: 4}]")
    results = rules.run_python_cases(current())
    assert [(r.rule, r.status) for r in results] == [("BR-T-001", "ok")]
    assert "sample_double" not in rules.generate_unit_tests(current())


def test_failing_python_case_shows_the_difference(minimal_project):
    _rule(minimal_project.root, "[{value: 2, double: 5}, {value: 5, double: 10}]")
    (r,) = rules.run_python_cases(current())
    assert r.status == "failed" and "expected" in r.message


def test_case_requiring_missing_pspp(minimal_project, monkeypatch):
    monkeypatch.setenv("STATERA_PSPP", "nonexistent-command")
    monkeypatch.delenv("STATERA_REQUIRE", raising=False)  # the CI sets it for the whole job
    _rule(minimal_project.root, "[{value: 2, double: 4}, {value: 5, double: 10}]", requires="requires: [pspp]\n")
    assert rules.run_python_cases(current())[0].status == "not-run"
    monkeypatch.setenv("STATERA_REQUIRE", "pspp")  # in the CI: tool required
    assert rules.run_python_cases(current())[0].status == "failed"
