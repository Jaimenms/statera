"""The content repository: `statera.toml` and the units it contains.

Structure (specification v0, `specification/01-content-repository.md`):

    statera.toml              organization identity, namespace, engine, editions register
    governance/               roles, shared owners, ADRs, cross-cutting processes
    references/<name>/        official reference table: CSV + ODCS contract with authority and temporality
    products/<name>/          data products with the GSBPM phases that apply to them. By alignment:
                                source     internal, incremental extraction from a system (bronze)
                                aggregate  internal, canonical entities per cut (silver)
                                consumer   published, dissemination tables (gold)
    queries/                  recurring queries that span products
    recipes/                  executable, tested examples
    dbt_project.yml           the organization's dbt project, at the root (GENERATED), with packages.yml
                              and profiles.yml
    dbt/                      dbt infrastructure: macros/, generated/, dbt_packages/, data/ (engine)
    docs/                     GENERATED documentation

A UNIT is a product or a reference table. Each unit has an owner, contracts and, in the phases that
apply to it, a `data/` folder (outside git) with what each run produces. The domain (owning area) is
the `domain` attribute of the ODPS descriptor, not a folder (ADR 0016).

Environment variables:
    STATERA_PROJECT   content repository root (default: search for statera.toml, or content/statera.toml,
                      from the cwd upward, so commands work from any folder of the data repository)
    STATERA_DATA      moves the base of ALL data/ folders (useful in tests)
    STATERA_REGISTRY  changes the location of the editions register
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from dataclasses import dataclass, field
from functools import cache, cached_property
from pathlib import Path

import yaml

PACKAGE = Path(__file__).resolve().parent
SCHEMAS = PACKAGE / "schemas"
STANDARD_SCHEMAS = SCHEMAS / "standards"
STATERA_SCHEMAS = SCHEMAS / "statera"
DBT_MACROS = PACKAGE / "dbt"

PHASES = {
    1: "1-specify",
    2: "2-design",
    3: "3-build",
    4: "4-collect",
    5: "5-process",
    6: "6-analyse",
    7: "7-disseminate",
    8: "8-evaluate",
}
TYPE_FOLDERS = {"product": "products", "reference": "references"}
DESCRIPTORS = {"product": "1-specify/product.odps.yaml"}
ALIGNMENTS = ("source", "aggregate", "consumer")
# Tableau 10 (distinguishable, colorblind-friendly); gray is reserved for reference tables
PRODUCT_PALETTE = (
    "#4E79A7", "#F28E2B", "#59A14F", "#E15759", "#B07AA1",
    "#76B7B2", "#EDC948", "#FF9DA7", "#9C755F", "#3D6B8E",
)  # fmt: skip
REFERENCE_COLOR = "#8C8C8C"
VISIBILITIES = ("internal", "public")


class ProjectError(RuntimeError):
    """Content repository missing or misconfigured."""


def read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# Product skeleton (specification 01): every product has these files, either with content or with
# `not_applicable: <reason>`. That way the structure is the same in all of them, and a file that does
# not apply says why, instead of being missing (absence is ambiguous: does it not apply, or was it forgotten?).
SKELETON = {
    "1-specify/calendar.yaml": "calendar",
    "2-design/process.yaml": "process",
    "6-analyse/controls.yaml": "controls",
    "7-disseminate/series.yaml": "series",
    "8-evaluate/indicators.yaml": "indicators",
}


def not_applicable(path: Path) -> str | None:
    """Reason declared in a file that does not apply to the product (`not_applicable: <reason>`);
    None if the file does not exist or has content."""
    if not path.exists():
        return None
    data = read_yaml(path)
    if isinstance(data, dict) and set(data) == {"not_applicable"}:
        return str(data["not_applicable"] or "")
    return None


@dataclass(frozen=True)
class Unit:
    """Product (dissemination or internal) or reference table."""

    kind: str
    name: str
    folder: Path
    project: Project = field(compare=False, repr=False)

    def phase(self, number: int) -> Path:
        return self.folder / PHASES[number]

    def data(self, number: int) -> Path:
        """The phase's data/ folder (outside git). With STATERA_DATA, it lives under that base."""
        base = os.environ.get("STATERA_DATA")
        if base:
            return Path(base).resolve() / TYPE_FOLDERS[self.kind] / self.name / PHASES[number]
        return self.phase(number) / "data"

    @property
    def relative(self) -> str:
        return self.folder.relative_to(self.project.root).as_posix()

    @property
    def urn(self) -> str:
        return f"{self.project.namespace}.{self.name}"

    @property
    def descriptor(self) -> Path | None:
        return self.folder / DESCRIPTORS[self.kind] if self.kind in DESCRIPTORS else None

    @property
    def odps(self) -> dict:
        return self.read(DESCRIPTORS["product"]) or {} if self.kind == "product" else {}

    def custom_property(self, name: str, default=None):
        for item in self.odps.get("customProperties", []) or []:
            if item.get("property") == name:
                return item.get("value")
        return default

    @property
    def alignment(self) -> str:
        """`source` | `aggregate` | `consumer` (default: `consumer`)."""
        return self.custom_property("alignment", "consumer") if self.kind == "product" else "reference"

    @property
    def internal(self) -> bool:
        return self.kind == "product" and self.custom_property("visibility", "public") == "internal"

    def read(self, relative: str):
        """Reads a YAML file of the unit (e.g. '1-specify/calendar.yaml'); None if it does not exist or
        declares `not_applicable`."""
        path = self.folder / relative
        return read_yaml(path) if self.has(relative) else None

    def has(self, relative: str) -> bool:
        """The file exists and has content (does not declare `not_applicable`)."""
        path = self.folder / relative
        return path.exists() and not_applicable(path) is None

    def not_applicable(self, relative: str) -> str | None:
        return not_applicable(self.folder / relative)

    def contracts(self) -> list[Path]:
        return sorted(c for c in self.folder.rglob("*.odcs.yaml") if "data" not in c.parts)

    def processes(self) -> list[Path]:
        """The product's process (one per product), when it has content."""
        return [self.process] if self.process.exists() and not_applicable(self.process) is None else []

    @property
    def process(self) -> Path:
        """`2-design/process.yaml`: the product's single process, which is also the run plan (specification 05).
        A repository of statera 0.5 or earlier (`2-design/processes/to-be.yaml`) still runs; validation says
        how to move it."""
        single = self.phase(2) / "process.yaml"
        legacy = self.legacy_processes_folder / "to-be.yaml"
        return legacy if not single.exists() and legacy.exists() else single

    @property
    def legacy_processes_folder(self) -> Path:
        """`2-design/processes/` of statera 0.5 and earlier (as-is and to-be processes)."""
        return self.phase(2) / "processes"

    @property
    def to_be_process(self) -> Path:  # statera 0.5 name, kept for code that still uses it
        return self.process

    @property
    def runs_folder(self) -> Path:
        """Log of the process runs (duration, state, OpenLineage): input to phase 8."""
        return self.data(8) / "runs"


class Project:
    def __init__(self, root: Path):
        self.root = root.resolve()
        file = self.root / "statera.toml"
        if not file.exists():
            raise ProjectError(f"{file} not found")
        self.config = tomllib.loads(file.read_text(encoding="utf-8"))

    def __repr__(self) -> str:
        return f"Project({self.root})"

    # --- identity --------------------------------------------------------------------------
    def _section(self, name: str) -> dict:
        return self.config.get(name, {})

    @property
    def name(self) -> str:
        return self._section("project").get("name", self.root.name)

    @property
    def organization(self) -> str:
        return self._section("project").get("organization", "")

    @property
    def acronym(self) -> str:
        return self._section("project")["acronym"]

    @property
    def namespace(self) -> str:
        return self._section("project")["namespace"]

    @property
    def required_version(self) -> str | None:
        """Statera versions this repository accepts (`[project] required_version`, e.g. ">=0.4,<0.5")."""
        return self._section("project").get("required_version")

    @property
    def conventions(self) -> dict:
        """Data convention columns ([conventions] in statera.toml): the reference period column and the
        cut-off date column. Column names belong to the content (in its language); the default is English."""
        c = self._section("conventions")
        return {
            "period_column": c.get("period_column", "period"),
            "cut_date_column": c.get("cut_date_column", "cut_date"),
            "valid_from_column": c.get("valid_from_column", "valid_from"),
            "valid_to_column": c.get("valid_to_column", "valid_to"),
        }

    @property
    def synthetic(self) -> bool:
        return bool(self._section("project").get("synthetic", False))

    # --- units -----------------------------------------------------------------------------
    @cached_property
    def units(self) -> list[Unit]:
        found = []
        for kind, folder in TYPE_FOLDERS.items():
            base = self.root / folder
            if not base.is_dir():
                continue
            for sub in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
                if kind in DESCRIPTORS and not (sub / DESCRIPTORS[kind]).exists():
                    continue
                if kind == "reference" and not any(sub.glob("*.odcs.yaml")):
                    continue
                found.append(Unit(kind, sub.name, sub, self))
        return found

    def _of_type(self, kind: str) -> list[Unit]:
        return [u for u in self.units if u.kind == kind]

    def products(self) -> list[Unit]:
        return self._of_type("product")

    def references(self) -> list[Unit]:
        return self._of_type("reference")

    def unit(self, name: str, kind: str | None = None) -> Unit:
        for u in self.units:
            if u.name == name and (kind is None or u.kind == kind):
                return u
        raise ProjectError(f"Unit '{name}' ({kind or 'any kind'}) not found in {self.root}")

    def product(self, name: str) -> Unit:
        return self.unit(name, "product")

    def unit_of(self, path: Path) -> Unit | None:
        path = path.resolve()
        for u in self.units:
            if path == u.folder or u.folder in path.parents:
                return u
        return None

    def product_colors(self) -> dict[str, str]:
        """One color per product (alphabetical order over a colorblind-safe palette), the same in the dbt
        catalog (`docs.node_color`) and in the generated lineage; reference tables are gray."""
        return {u.name: PRODUCT_PALETTE[i % len(PRODUCT_PALETTE)] for i, u in enumerate(self.products())}

    # --- cross-cutting folders ------------------------------------------------------------
    @property
    def governance(self) -> Path:
        return self.root / "governance"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def recipes(self) -> Path:
        return self.root / "recipes"

    def query_folders(self) -> list[Path]:
        """`queries/`: every recurring query, by theme; the product it serves is its `product` axis."""
        return [self.root / "queries"]

    def legacy_query_folders(self) -> list[Path]:
        """`products/<p>/queries/` of statera 0.6 and earlier: no longer read (validation says how to move)."""
        return [p.folder / "queries" for p in self.products() if (p.folder / "queries").is_dir()]

    def processes(self) -> list[Path]:
        cross_cutting = sorted((self.governance / "processes").glob("*.yaml"))
        return [*(c for u in self.units for c in u.processes()), *cross_cutting]

    def rule_folders(self) -> list[Path]:
        return [u.phase(5) / "rules" for u in self.units if (u.phase(5) / "rules").is_dir()]

    def owners(self) -> list[Path]:
        """shared-owners.yaml and the owners.yaml of each unit with phase 1."""
        units = [u.phase(1) / "owners.yaml" for u in self.units]
        return [self.governance / "shared-owners.yaml", *(c for c in units if c.exists())]

    # --- engine and dbt --------------------------------------------------------------------
    @property
    def engine(self) -> dict:
        return {"type": "duckdb", "path": "dbt/data/warehouse.duckdb", **self._section("engine")}

    @property
    def consumption(self) -> dict:
        """[consumption]: where those who only read the data read it from (statera.consume.connection)."""
        return self._section("consumption")

    @property
    def warehouse(self) -> Path:
        base = os.environ.get("STATERA_DATA")
        name = Path(self.engine["path"]).name
        return Path(base).resolve() / "engine" / name if base else self.root / self.engine["path"]

    @property
    def dbt_project(self) -> Path:
        """Folder of dbt_project.yml, packages.yml and profiles.yml: the repository root.

        Models, seeds and tests live in the units; with the project at the root, they are all INSIDE
        the dbt project, as the dbt guide and the tooling (dbt Power User) expect (ADR 0017).
        """
        return self.root

    @property
    def dbt_folder(self) -> Path:
        """dbt infrastructure that does not belong to the units: macros/, generated/, dbt_packages/."""
        return self.root / self._section("dbt").get("folder", "dbt")

    @property
    def dbt_name(self) -> str:
        return self._section("dbt").get("project", self.name.replace("-", "_"))

    @property
    def dbt_profile(self) -> str:
        return self._section("dbt").get("profile", self.name.replace("-", "_"))

    @property
    def dbt_manifest(self) -> Path:
        return self.dbt_project / "target" / "manifest.json"

    @property
    def dbt_results(self) -> Path:
        return self.dbt_project / "target" / "run_results.json"

    # --- register, lineage -------------------------------------------------------------------
    @property
    def edition_registry(self) -> Path:
        default = self._section("registry").get("editions", "../publications")
        return Path(os.environ.get("STATERA_REGISTRY") or self.root / default).resolve()

    @property
    def openlineage_namespace(self) -> str:
        return self._section("openlineage").get("namespace", self.namespace)

    @property
    def data_namespace(self) -> str:
        return f"{self.engine['type']}://{Path(self.engine['path']).name}"

    # --- utilities -------------------------------------------------------------------------
    def rel(self, path: Path) -> str:
        """Path relative to the root (or absolute, if it is outside it, as in tests)."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def data_folders(self) -> list[Path]:
        folders = [u.data(n) for u in self.units for n in PHASES if u.data(n).exists()]
        return [*folders, self.warehouse.parent]

    def hook(self, specification: str):
        """Loads a content function declared as 'path/file.py:function'."""
        file, _, function = specification.partition(":")
        path = self.root / file
        spec = importlib.util.spec_from_file_location(f"statera_hook_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ProjectError(f"Hook {specification} could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses and annotations need the module registered
        spec.loader.exec_module(module)
        return getattr(module, function)

    @property
    def demo(self) -> dict:
        return self._section("demo")


def discover_root(start: Path | None = None) -> Path:
    if os.environ.get("STATERA_PROJECT"):
        return Path(os.environ["STATERA_PROJECT"]).resolve()
    current_ = (start or Path.cwd()).resolve()
    for folder in (current_, *current_.parents):
        if (folder / "statera.toml").exists():
            return folder
        if (folder / "content" / "statera.toml").exists():
            return folder / "content"
    raise ProjectError("statera.toml not found: run the command inside a data repository (or set STATERA_PROJECT)")


@cache
def _load(root: Path, _data: str | None, _registry: str | None) -> Project:
    return Project(root)


def current() -> Project:
    """The project of the current directory. Reloads if the environment variables change (tests)."""
    return _load(discover_root(), os.environ.get("STATERA_DATA"), os.environ.get("STATERA_REGISTRY"))
