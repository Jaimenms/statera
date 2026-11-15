"""Recipes: short, complete examples, run in CI so they do not go stale.

Each recipe is a file in `recipes/`:
  - `.py` in cell format (`# %%`, compatible with Jupytext and VS Code), which uses the
    library (`statera.consume.catalog`);
  - `.sql`, run directly on the engine (read-only).

The header declares the title, layers read and classification, in comment lines:

    # Recipe: Balance time series by UF
    # Layers: gold
    # Classification: broad · aggregate
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from statera.consume import connection
from statera.project import Project, current

HEADER = re.compile(r"^(?:#|--)\s*(Recipe|Layers|Classification|Library|Authors):\s*(.+)$")


@dataclass
class Recipe:
    path: Path
    meta: dict

    @property
    def title(self) -> str:
        return self.meta.get("recipe", self.path.stem)


def _header(path: Path) -> dict:
    meta = {}
    for line in path.read_text(encoding="utf-8").splitlines()[:15]:
        if finding := HEADER.match(line.strip()):
            key = finding.group(1).lower()
            meta[key] = finding.group(2).strip()
    return meta


def load(project: Project | None = None) -> list[Recipe]:
    folder = (project or current()).recipes
    files = sorted([*folder.glob("*.py"), *folder.glob("*.sql")]) if folder.is_dir() else []
    return [Recipe(a, _header(a)) for a in files]


def run(recipe: Recipe, project: Project | None = None, engine: str | None = None) -> tuple[bool, str]:
    """Runs the recipe against the engine (statera.consume.connection). Returns (success, output)."""
    project = project or current()
    if recipe.path.suffix == ".sql":
        try:
            df = connection.fetch(recipe.path.read_text(encoding="utf-8"), project=project, engine=engine)
        except Exception as error:  # noqa: BLE001 (any engine's error is the recipe's failure)
            return False, str(error)
        return True, df.to_string(index=False)
    # deprecation warnings from third-party libraries clutter the output; classification warnings are kept
    env = {
        **os.environ,
        "STATERA_PROJECT": str(project.root),
        **({connection.ENV: engine} if engine else {}),
        "PYTHONWARNINGS": "ignore::DeprecationWarning,ignore::FutureWarning",
    }
    process = subprocess.run(
        [sys.executable, str(recipe.path)], cwd=project.root, env=env, capture_output=True, text=True
    )
    return process.returncode == 0, (process.stdout + process.stderr).strip()


def header_problems(project: Project | None = None) -> list[str]:
    return [
        f"{r.path.name}: header missing {field}"
        for r in load(project)
        for field in ("recipe", "layers", "classification")
        if field not in r.meta
    ]
