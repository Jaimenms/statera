"""Legacy R script: the wiring (.rds inputs, inputs/output variables, errors, output) is tested with a
simulated Rscript on any machine; real R, only where it is available (the CI installs it)."""

import shutil
import sys
import textwrap

import pandas as pd
import pytest

from statera.legacy import r

FAKE_R = textwrap.dedent(
    """
    import re, shutil, sys
    if "--version" in sys.argv:
        print("Rscript (R) version 0.0 (simulated)", file=sys.stderr); sys.exit(0)
    script = open(sys.argv[-1], encoding="utf-8").read()
    if "stop(" in script:
        print("Error: simulated error in the script", file=sys.stderr); sys.exit(1)
    inputs = dict(re.findall(r'^  (\\w+) = "([^"]+)"', script, re.M))
    output = re.search(r'^output <- "([^"]+)"', script, re.M).group(1)
    source = re.search(r"readRDS\\(inputs\\$(\\w+)\\)", script).group(1)
    shutil.copy(inputs[source], output)  # the fake only copies: it does not interpret R
    """
)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    stub = tmp_path / "fake_rscript.py"
    stub.write_text(FAKE_R, encoding="utf-8")
    monkeypatch.setenv("STATERA_R", f"{sys.executable} {stub}")
    script = tmp_path / "copy.R"
    script.write_text("records <- readRDS(inputs$records)\nsaveRDS(records, output)\n", encoding="utf-8")
    return script


def test_round_trip_through_rds(fake):
    df = pd.DataFrame({"uf_abbr": ["SP", "RJ"], "records": [3, 5]})
    output = r.run(fake, {"records": df})
    assert output["uf_abbr"].tolist() == ["SP", "RJ"] and output["records"].tolist() == [3, 5]
    assert r.version() == "Rscript (R) version 0.0 (simulated)"


def test_r_error_becomes_exception(fake, tmp_path):
    bad = tmp_path / "bad.R"
    bad.write_text('records <- readRDS(inputs$records)\nstop("failed")\n', encoding="utf-8")
    with pytest.raises(r.RError, match="simulated error"):
        r.run(bad, {"records": pd.DataFrame({"a": [1]})})


def test_without_r_or_with_invalid_name(monkeypatch, tmp_path):
    with pytest.raises(r.RError, match="invalid R name"):
        r.run(tmp_path / "x.R", {"1records": pd.DataFrame({"a": [1]})})
    monkeypatch.setenv("STATERA_R", "nonexistent-command")
    assert not r.available()
    with pytest.raises(r.RError, match="not found"):
        r.run(tmp_path / "x.R", {"records": pd.DataFrame({"a": [1]})})


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R not installed (the CI installs it)")
def test_real_script_in_r(tmp_path, monkeypatch):
    monkeypatch.delenv("STATERA_R", raising=False)
    script = tmp_path / "median.R"
    script.write_text(
        "records <- readRDS(inputs$records)\n"
        "result <- aggregate(value ~ uf_abbr, data = records, FUN = median)\n"
        "saveRDS(result, output)\n",
        encoding="utf-8",
    )
    df = pd.DataFrame({"uf_abbr": ["SP", "SP", "SP", "RJ"], "value": [10.0, 30.0, 20.0, 5.0]})
    output = r.run(script, {"records": df}).sort_values("uf_abbr")
    assert output["value"].tolist() == [5.0, 20.0]
