"""Legacy R script run by Rscript, called from a dbt Python model.

Same structure as PSPP (`statera.legacy.pspp`), so that a legacy `.R` script runs inside the
process without being rewritten:

    # products/<p>/5-process/models/marts/<p>/<model>.py
    from pathlib import Path
    from statera.legacy import r

    SCRIPT = Path(__file__).with_name("<model>.R")

    def transform(inputs, variables):
        return r.run(SCRIPT, {"records": inputs["int_x__records"]}, output="result")

    def model(dbt, session):
        return transform({"int_x__records": dbt.ref("int_x__records").df()}, {})

The only adaptation of the legacy script is replacing file paths with those the framework defines
at the start of the script: each input becomes an `.rds` in `inputs$<name>` (`readRDS(inputs$records)`)
and the result is written to `output` (`saveRDS(result, output)`). Base R only: the framework requires
no package; those the script uses are its own and must be installed in the environment.

The command is `Rscript --vanilla` (no saved profile or workspace, so that the run does not depend
on the machine), or whatever the STATERA_R variable says (e.g. a container with a pinned version).
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

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class RError(RuntimeError):
    """R is not available or the script ended with an error."""


def command() -> list[str]:
    return shlex.split(os.environ.get("STATERA_R", "Rscript"))


def available() -> bool:
    return shutil.which(command()[0]) is not None


def version() -> str:
    if not available():
        return "unavailable"
    process = subprocess.run([*command(), "--version"], capture_output=True, text=True, timeout=60)
    text = (process.stdout or process.stderr).strip()  # Rscript writes the version to stderr
    return text.splitlines()[0] if process.returncode == 0 and text else "?"


def _r_text(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def run(script: Path, inputs: dict[str, pd.DataFrame], output: str = "result", timeout: int = 900):
    """Runs the R script with the inputs (DataFrames) and returns the data.frame saved to `output`."""
    try:
        import pyreadr
    except ImportError as error:
        raise RError(
            "pyreadr is not installed: declare legacy-r = ['statera[legacy-r]'] in [dependency-groups]"
            " of the data repository's pyproject.toml (then any production command installs it)"
        ) from error

    for name in [*inputs, output]:
        if not NAME.match(name):
            raise RError(f"invalid R name: {name!r}")
    if not available():
        raise RError(
            f"R not found ({command()[0]}). Install R (apt install r-base-core; brew install r) or "
            "point STATERA_R to the command."
        )
    with tempfile.TemporaryDirectory(prefix="statera-r-") as folder:
        folder = Path(folder)
        for name, df in inputs.items():
            pyreadr.write_rds(str(folder / f"{name}.rds"), df)
        output_file = folder / f"{output}.rds"
        header = [
            "# Defined by the Statera framework: inputs (.rds) and output file.",
            "inputs <- list(",
            ",\n".join(f"  {name} = {_r_text(folder / f'{name}.rds')}" for name in inputs),
            ")",
            f"output <- {_r_text(output_file)}",
        ]
        executable = folder / "run.R"
        executable.write_text("\n".join(header) + "\n" + Path(script).read_text(encoding="utf-8"), encoding="utf-8")
        try:
            process = subprocess.run(
                [*command(), "--vanilla", str(executable)], capture_output=True, text=True, timeout=timeout, cwd=folder
            )
        except subprocess.TimeoutExpired as error:
            raise RError(f"{Path(script).name}: R exceeded {timeout} s") from error
        if process.returncode != 0 or not output_file.exists():
            detail = (process.stderr or process.stdout)[-1500:].strip()
            raise RError(f"{Path(script).name}: R ended with an error (code {process.returncode}):\n  {detail}")
        return next(iter(pyreadr.read_r(str(output_file)).values()))
