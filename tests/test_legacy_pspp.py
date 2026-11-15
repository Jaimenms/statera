"""Legacy SPSS syntax via PSPP: the wiring (.sav inputs, FILE HANDLE, errors, output) is tested with a
simulated PSPP on any machine; the real syntax, only where PSPP is available (the CI installs it)."""

import shutil
import sys
import textwrap

import pandas as pd
import pytest

from statera.legacy import pspp

FAKE_PSPP = textwrap.dedent(
    """
    import re, shutil, sys
    if "--version" in sys.argv:
        print("pspp (simulated) 0.0"); sys.exit(0)
    script = open(sys.argv[-1], encoding="utf-8").read()
    if "SIMULATED_ERROR" in script:
        print("run.sps:5: error: SIMULATED: syntax error"); sys.exit(0)
    handles = dict(re.findall(r"FILE HANDLE (\\w+) /NAME='([^']+)'", script))
    source = re.search(r"GET FILE=(\\w+)", script).group(1)
    target = re.search(r"SAVE OUTFILE=(\\w+)", script).group(1)
    shutil.copy(handles[source], handles[target])  # the fake only copies: it does not interpret SPSS
    """
)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    stub = tmp_path / "fake_pspp.py"
    stub.write_text(FAKE_PSPP, encoding="utf-8")
    monkeypatch.setenv("STATERA_PSPP", f"{sys.executable} {stub}")
    syntax = tmp_path / "copy.sps"
    syntax.write_text("GET FILE=records.\nSAVE OUTFILE=result.\n", encoding="utf-8")
    return syntax


def test_round_trip_through_sav(fake):
    df = pd.DataFrame({"uf_abbr": ["SP", "RJ"], "records": [3, 5]})
    output = pspp.run(fake, {"records": df})
    assert output["uf_abbr"].tolist() == ["SP", "RJ"] and output["records"].tolist() == [3, 5]
    assert pspp.version() == "pspp (simulated) 0.0"


def test_listing_error_becomes_exception(fake, tmp_path):
    bad = tmp_path / "bad.sps"
    bad.write_text("* SIMULATED_ERROR.\nGET FILE=records.\nSAVE OUTFILE=result.\n", encoding="utf-8")
    with pytest.raises(pspp.PSPPError, match="SIMULATED"):
        pspp.run(bad, {"records": pd.DataFrame({"a": [1]})})


def test_without_pspp_or_with_invalid_handle(monkeypatch, tmp_path):
    with pytest.raises(pspp.PSPPError, match="invalid SPSS handle name"):
        pspp.run(tmp_path / "x.sps", {"1records": pd.DataFrame({"a": [1]})})
    monkeypatch.setenv("STATERA_PSPP", "nonexistent-command")
    assert not pspp.available()
    with pytest.raises(pspp.PSPPError, match="not found"):
        pspp.run(tmp_path / "x.sps", {"records": pd.DataFrame({"a": [1]})})


@pytest.mark.skipif(shutil.which("pspp") is None, reason="GNU PSPP not installed (the CI installs it)")
def test_real_syntax_in_pspp(tmp_path, monkeypatch):
    monkeypatch.delenv("STATERA_PSPP", raising=False)
    syntax = tmp_path / "aggregate.sps"
    syntax.write_text(
        "GET FILE=records.\n"
        "AGGREGATE OUTFILE=* /BREAK=uf_abbr /records=N /total_value=SUM(value).\n"
        "COMPUTE mean_value = total_value / records.\n"
        "SAVE OUTFILE=result.\n",
        encoding="utf-8",
    )
    df = pd.DataFrame({"uf_abbr": ["SP", "SP", "RJ"], "value": [10.0, 30.0, 5.0]})
    output = pspp.run(syntax, {"records": df}).sort_values("uf_abbr")
    assert output["records"].tolist() == [1, 2] and output["mean_value"].tolist() == [5.0, 20.0]
