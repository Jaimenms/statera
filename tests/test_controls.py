"""Properties of the revision-between-editions control (Hypothesis)."""

import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from statera.analyse.controls import Inputs, revisions, run

values = st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=12)


@given(values)
def test_identical_editions_have_no_revision(hires):
    df = pd.DataFrame({"period": [f"2026-{i + 1:02d}" for i in range(len(hires))], "hires": hires})
    r = revisions(df, df.copy(), ["period"], ["hires"], "hires")
    assert (r["revision_hires"] == 0).all() and (r["relative_revision"] == 0).all()


@given(st.integers(min_value=1, max_value=10_000), st.integers(min_value=0, max_value=10_000))
def test_relative_revision_is_against_the_previous_edition(before, after):
    current = pd.DataFrame({"period": ["2026-01"], "hires": [after]})
    previous = pd.DataFrame({"period": ["2026-01"], "hires": [before]})
    r = revisions(current, previous, ["period"], ["hires"], "hires")
    assert abs(r["relative_revision"].iloc[0] - abs(after - before) / before) < 1e-9


def test_without_previous_edition_the_control_passes():
    current = pd.DataFrame({"period": ["2026-01"], "hires": [5]})
    definition = {
        "id": "CK-04",
        "name": "Revision",
        "type": "revision-between-editions",
        "port": "p",
        "key": ["period"],
        "measures": ["hires"],
        "relative_base": "hires",
        "warning_threshold": 0.03,
        "error_threshold": 0.1,
    }
    results, tables = run([definition], Inputs("2026-01", lambda p: current, lambda p: None))
    assert results[0].ok and tables == {}
