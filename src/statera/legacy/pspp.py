"""Legacy SPSS syntax run by GNU PSPP, called from a dbt Python model.

So that a legacy `.sps` script runs inside the process without being rewritten:

    # products/<p>/5-process/models/marts/<p>/<model>.py
    from pathlib import Path
    from statera.legacy import pspp

    SYNTAX = Path(__file__).with_name("<model>.sps")

    def transform(inputs, variables):
        return pspp.run(SYNTAX, {"records": inputs["int_x__records"]}, output="result")

    def model(dbt, session):
        return transform({"int_x__records": dbt.ref("int_x__records").df()}, {})

The only adaptation of the legacy script is replacing file paths with FILE HANDLEs, which the
framework defines: each input becomes a `.sav` file named after it (`GET FILE=records.`) and
the result is read from the output handle (`SAVE OUTFILE=result.`). The rest of the syntax is unchanged.

PSPP is not SPSS: each script needs evidence that PSPP runs it as SPSS does before it can serve
as a reference (certification against SPSS or against published editions; foundations, §PSPP).
The command is `pspp`, or whatever the STATERA_PSPP variable says (e.g. a container).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

HANDLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ERROR = re.compile(r"\berror:", re.IGNORECASE)


class PSPPError(RuntimeError):
    """PSPP is not available or the syntax ended with an error."""


def command() -> list[str]:
    return shlex.split(os.environ.get("STATERA_PSPP", "pspp"))


def available() -> bool:
    return shutil.which(command()[0]) is not None


def version() -> str:
    if not available():
        return "unavailable"
    process = subprocess.run([*command(), "--version"], capture_output=True, text=True, timeout=60)
    return (process.stdout or process.stderr).splitlines()[0].strip() if process.returncode == 0 else "?"


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def run(syntax: Path, inputs: dict[str, pd.DataFrame], output: str = "result", timeout: int = 900):
    """Runs the syntax in PSPP with the inputs (DataFrames) and returns the dataset saved to `output`."""
    try:
        import pyreadstat
    except ImportError as error:
        raise PSPPError(
            "pyreadstat is not installed: declare legacy-spss = ['statera[legacy-spss]'] in [dependency-groups]"
            " of the data repository's pyproject.toml (then any production command installs it)"
        ) from error

    for name in [*inputs, output]:
        if not HANDLE_NAME.match(name):
            raise PSPPError(f"invalid SPSS handle name: {name!r}")
    if not available():
        raise PSPPError(
            f"PSPP not found ({command()[0]}). Install GNU PSPP (apt install pspp) or point "
            "STATERA_PSPP to the command."
        )
    with tempfile.TemporaryDirectory(prefix="statera-pspp-") as folder:
        folder = Path(folder)
        for name, df in inputs.items():
            pyreadstat.write_sav(df, str(folder / f"{name}.sav"))
        handles = [f"FILE HANDLE {name} /NAME={_quote(folder / f'{name}.sav')}." for name in [*inputs, output]]
        script = folder / "run.sps"
        script.write_text("\n".join(handles) + "\n" + Path(syntax).read_text(encoding="utf-8"), encoding="utf-8")
        listing = folder / "listing.txt"
        try:
            process = subprocess.run(
                [*command(), "-o", str(listing), str(script)], capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as error:
            raise PSPPError(f"{Path(syntax).name}: PSPP exceeded {timeout} s") from error
        text = process.stdout + process.stderr + (listing.read_text(errors="replace") if listing.exists() else "")
        errors = [line.strip() for line in text.splitlines() if ERROR.search(line)]
        output_file = folder / f"{output}.sav"
        if process.returncode != 0 or errors or not output_file.exists():
            detail = "\n  ".join(errors[:10]) or text[-1500:]
            raise PSPPError(f"{Path(syntax).name}: PSPP ended with an error (code {process.returncode}):\n  {detail}")
        data, _ = pyreadstat.read_sav(str(output_file))
        return data
