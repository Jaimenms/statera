"""`statera doctor`: checks the computer and the data repository, and says how to fix each problem.

Covers what usually breaks on a new machine: uv and git on the PATH, the Statera version (pinned,
required by statera.toml, and the global launcher), the repository layout and environment, line endings
and, on Windows, long paths; where the data is read from. The production tools (dbt; GNU PSPP and R, only
when the repository has `.sps` or `.R` models) are optional: someone who only reads the data does not
need them, and their absence is reported as information, not as a problem.
"""

from __future__ import annotations

import os
import platform as _os_platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from statera import __version__, launcher

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
SYMBOL = {OK: "✔", WARN: "!", FAIL: "✘", INFO: "·"}
WINDOWS = os.name == "nt"


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""
    fix: str = ""


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def _git_config(key: str, cwd: Path | None) -> str:
    r = _run(["git", "config", "--get", key], cwd)
    return r.stdout.strip().lower() if r and r.returncode == 0 else ""


def _tools() -> list[Check]:
    checks = []
    uv = shutil.which("uv")
    if uv:
        r = _run([uv, "--version"])
        checks.append(Check(OK, "uv", r.stdout.strip() if r else uv))
    else:
        checks.append(Check(FAIL, "uv", "not found on the PATH", "install uv: https://docs.astral.sh/uv/"))
    if shutil.which("git"):
        r = _run(["git", "--version"])
        checks.append(Check(OK, "git", r.stdout.strip() if r else "found"))
    else:
        checks.append(Check(FAIL, "git", "not found on the PATH", "install git: https://git-scm.com/downloads"))
    checks.append(Check(INFO, "python", f"{_os_platform.python_version()} ({sys.executable})"))
    return checks


def _versions(root: Path | None) -> list[Check]:
    checks = []
    info = launcher.launcher()
    where = "the repository's pinned version" if root and launcher.in_repository_environment(root) else "this copy"
    detail = f"{__version__} ({where})"
    if info:
        detail += f"; handed off by the global statera {info[0]}"
    checks.append(Check(OK, "statera", detail))
    if problem := launcher.launcher_problem():
        checks.append(Check(FAIL, "global statera", problem, "uv tool upgrade statera"))
    if root is not None:
        try:
            from statera.project import current

            required = current().required_version
        except Exception:
            required = None
        if problem := launcher.version_problem(required):
            checks.append(Check(FAIL, "required version", problem, "uv sync"))
        elif required:
            checks.append(Check(OK, "required version", f"{required} (statera.toml)"))
        else:
            checks.append(
                Check(
                    WARN,
                    "required version",
                    "statera.toml does not declare [project] required_version",
                    f'add required_version = ">={__version__},<{_next_minor(__version__)}" to [project]',
                )
            )
    if not shutil.which("statera") and not os.environ.get(launcher.ENV_HANDOFF):
        checks.append(
            Check(
                INFO,
                "global statera",
                "not installed (optional: `uv run statera ...` also works)",
                "uv tool install git+ssh://git@github.com/Jaimenms/statera.git",
            )
        )
    return checks


def _next_minor(version: str) -> str:
    major, minor, *_ = (version.split(".") + ["0", "0"])[:3]
    return f"{major}.{int(minor) + 1}"


def _repository(root: Path | None) -> list[Check]:
    if root is None:
        return [Check(INFO, "data repository", "not inside a data repository (statera init <folder> creates one)")]
    checks = [Check(OK, "data repository", str(root))]
    if (root / "statera.toml").is_file() and (root.parent / "publications").is_dir():
        checks.append(
            Check(
                WARN,
                "layout",
                "pyproject.toml is inside content/ (layout of statera 0.1)",
                "move pyproject.toml, uv.lock and .python-version to the repository root (specification 01)",
            )
        )
    venv = root / ".venv"
    if not venv.is_dir():
        checks.append(Check(WARN, "environment", "no .venv yet", "uv sync (or just run any statera command)"))
    else:
        checks.append(Check(OK, "environment", str(venv)))
    if shutil.which("uv") and (root / "uv.lock").is_file():
        r = _run(["uv", "lock", "--check", "--project", str(root)])
        if r is not None and r.returncode != 0:
            checks.append(Check(WARN, "uv.lock", "out of date with pyproject.toml", "uv lock"))
        elif r is not None:
            checks.append(Check(OK, "uv.lock", "up to date"))
    elif not (root / "uv.lock").is_file():
        checks.append(Check(WARN, "uv.lock", "missing: the Statera version is not pinned", "uv lock"))
    tasks = root / ".vscode" / "tasks.json"
    checks.append(Check(INFO, "VS Code tasks", str(tasks) if tasks.is_file() else "none (optional)"))
    return checks


def _git(root: Path | None) -> list[Check]:
    if root is None or not shutil.which("git"):
        return []
    r = _run(["git", "rev-parse", "--show-toplevel"], root)
    if not r or r.returncode != 0:
        return [Check(WARN, "git repository", "the data repository is not under git", "git init -b main")]
    top = Path(r.stdout.strip())
    checks = [Check(OK, "git repository", str(top))]
    # the effective attribute of a repository file, honoring every .gitattributes on the way
    probe = "content/statera.toml" if (root / "content" / "statera.toml").is_file() else "statera.toml"
    r = _run(["git", "check-attr", "eol", "--", probe], root)
    lf = bool(r and r.returncode == 0 and r.stdout.strip().endswith(": lf"))
    if lf:
        checks.append(Check(OK, "line endings", "LF on every checkout (.gitattributes)"))
    else:
        checks.append(
            Check(
                WARN,
                "line endings",
                ".gitattributes does not set eol=lf: Windows checkouts may get CRLF and change hashes",
                'add "* text=auto eol=lf" to .gitattributes at the repository root',
            )
        )
    if _git_config("core.autocrlf", root) == "true" and not lf:
        checks.append(Check(WARN, "core.autocrlf", "true", "git config core.autocrlf false"))
    if WINDOWS:
        if _git_config("core.longpaths", root) == "true":
            checks.append(Check(OK, "long paths (git)", "core.longpaths = true"))
        else:
            checks.append(
                Check(
                    FAIL,
                    "long paths (git)",
                    "core.longpaths is off: deep product folders exceed 260 characters",
                    "git config --global core.longpaths true",
                )
            )
        checks += _windows_long_paths()
    return checks


def _windows_long_paths() -> list[Check]:
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            enabled = winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return []
    if enabled:
        return [Check(OK, "long paths (Windows)", "LongPathsEnabled = 1")]
    return [
        Check(
            WARN,
            "long paths (Windows)",
            "LongPathsEnabled = 0: Python tools may fail on paths over 260 characters",
            "as administrator: Set-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem "
            "LongPathsEnabled 1",
        )
    ]


def _groups(root: Path | None) -> set[str] | None:
    """Dependency groups declared in the repository's pyproject.toml (None when it cannot be read)."""
    import tomllib

    try:
        return set(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("dependency-groups", {}))
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        return None


# language, model extension, dependency group, framework module, reader package, program hint
LEGACY = [
    ("R", "*.R", "legacy-r", "r", "pyreadr", "install R (Rscript) or set STATERA_R"),
    ("GNU PSPP", "*.sps", "legacy-spss", "pspp", "pyreadstat", "install GNU PSPP or set STATERA_PSPP"),
]


def _production(root: Path | None) -> list[Check]:
    """Production tools (dbt, legacy code). Not needed to read the data: missing is not a problem."""
    import importlib

    groups = _groups(root) if root else None
    try:
        from dbt.version import __version__ as dbt_version
    except Exception:  # noqa: BLE001
        if groups is not None and "produce" not in groups:
            return [
                Check(
                    WARN,
                    "production tools",
                    "pyproject.toml has no `produce` group: production commands will not find dbt",
                    'add produce = ["statera[produce]"] to [dependency-groups] in pyproject.toml',
                )
            ]
        return [
            Check(
                INFO,
                "production tools",
                "not installed: enough to read the data (catalog, queries, metrics)",
                "the data team gets them with any production command (statera check, run, sync…) "
                "or `uv sync --all-groups`",
            )
        ]
    checks = [Check(OK, "dbt", dbt_version)]
    if root is None:
        return checks
    content = root / "content" if (root / "content" / "statera.toml").is_file() else root
    products = content / "products"
    for name, pattern, group, module_name, reader, hint in LEGACY:
        if not (products.is_dir() and any(products.rglob(pattern))):
            continue
        if groups is not None and group not in groups:
            checks.append(
                Check(
                    WARN,
                    name,
                    f"the products have {pattern[1:]} models, but pyproject.toml has no `{group}` group",
                    f'add {group} = ["statera[{group}]"] to [dependency-groups] in pyproject.toml',
                )
            )
            continue
        if importlib.util.find_spec(reader) is None:
            checks.append(Check(WARN, name, f"{reader} is not installed", "uv sync --all-groups"))
            continue
        module = importlib.import_module(f"statera.legacy.{module_name}")
        if module.available():
            checks.append(Check(OK, name, module.version()))
        else:
            checks.append(Check(WARN, name, "not found: legacy models and their rule cases will not run", hint))
    return checks


def _data(root: Path | None) -> list[Check]:
    """Where `statera query`, `metric` and the catalog read the data from."""
    if root is None:
        return []
    from statera.consume import connection
    from statera.project import current

    try:
        project = current()
    except Exception:  # noqa: BLE001 (an invalid statera.toml is reported by statera check)
        return []
    where = connection.describe(project)
    if connection.is_local(project) and not project.warehouse.exists():
        return [
            Check(
                WARN,
                "data",
                "nothing to read yet: no local engine and no shared engine declared",
                f"to query: ask the data team for the shared engine and set {connection.ENV} "
                "(or [consumption] engine in statera.toml); to produce: statera run",
            )
        ]
    connector = connection.connector_for(project)
    if connector:
        where += f" · connector {connector.origin}"
    try:
        con = connection.connect(project)
        con.disconnect()
    except Exception as error:  # noqa: BLE001
        return [Check(FAIL, "data", f"{where}: cannot connect ({str(error).splitlines()[0][:200]})")]
    return [Check(OK, "data", where)]


def run() -> list[Check]:
    root = launcher.repository_root()
    return [*_tools(), *_versions(root), *_repository(root), *_git(root), *_data(root), *_production(root)]


def report(checks: list[Check]) -> int:
    width = max(len(c.name) for c in checks)
    for c in checks:
        line = f"{SYMBOL[c.status]} {c.name:<{width}}  {c.detail}"
        print(line)
        if c.fix and c.status in (FAIL, WARN, INFO):
            print(f"  {'':<{width}}  → {c.fix}")
    failures = sum(c.status == FAIL for c in checks)
    warnings = sum(c.status == WARN for c in checks)
    print(f"\n{failures} problem(s), {warnings} warning(s)")
    return 1 if failures else 0
