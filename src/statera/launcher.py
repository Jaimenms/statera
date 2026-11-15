"""Hands a command off to the Statera version that the data repository pins.

Each data repository pins its own Statera version in uv.lock. A globally installed `statera`
(`uv tool install ...`) would otherwise run whatever version that computer happens to have, so validation
could pass locally with old rules and fail in CI. Instead, the global copy only finds the root of the data
repository and re-runs the command with `uv run --project <root> statera ...`: the pinned version runs, and
uv brings the environment up to date first if needed. Outside a data repository (e.g. `statera init`) it
runs itself. Same pattern as Gradle's gradlew, Node's corepack and rustup with rust-toolchain.toml.

What gets installed depends on the command. The data repository's dependencies are only what READING
the data needs (catalog, queries, metrics): someone outside the data team gets a small environment. The
production tools (dbt, Copier, the legacy-code readers) are dependency groups of the repository
(`produce`, `legacy-r`, `legacy-spss`, `dev`); the commands that produce (check, run, sync, docs…) are
handed off with `--all-groups`, so uv installs them the first time the data team needs them.

Because the global copy only finds the root and hands off, its own version rarely matters. The handoff
carries a protocol number; a pinned version that needs a newer launcher says so ("run uv tool upgrade
statera").

Environment variables:
    STATERA_HANDOFF     set on the handed-off process, so it never hands off again (no loop)
    STATERA_LAUNCHER    "<version>;<protocol>" of the global copy that handed off (shown by `statera doctor`)
    STATERA_NO_HANDOFF  set to 1 to always run this copy (debugging Statera itself)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from statera import __version__

PROTOCOL = 1  # bump when the handoff contract changes
MIN_LAUNCHER_PROTOCOL = 1  # oldest global launcher this version accepts
ENV_HANDOFF = "STATERA_HANDOFF"
ENV_LAUNCHER = "STATERA_LAUNCHER"
ENV_NO_HANDOFF = "STATERA_NO_HANDOFF"
LOCAL_COMMANDS = {"init"}  # never handed off: they create a repository, they don't run inside one
# Commands that only read: they run with the repository's dependencies alone (no production tools).
# Every other command produces or checks, and runs with all the repository's dependency groups.
CONSUMPTION_COMMANDS = {"doctor", "query", "metric", "recipes", "cuts", "status", "evaluate"}


def repository_root(start: Path | None = None) -> Path | None:
    """The uv project of the data repository: the first folder, from `start` upward, that has a
    pyproject.toml next to statera.toml or content/statera.toml. None outside a data repository."""
    current = (start or Path.cwd()).resolve()
    for folder in (current, *current.parents):
        if (folder / "pyproject.toml").is_file() and (
            (folder / "statera.toml").is_file() or (folder / "content" / "statera.toml").is_file()
        ):
            return folder
    return None


def in_repository_environment(root: Path) -> bool:
    """True when this process already runs from the repository's own environment (root/.venv)."""
    try:
        return Path(sys.prefix).resolve() == (root / ".venv").resolve()
    except OSError:
        return False


def handoff(argv: list[str]) -> int | None:
    """Re-runs the command with the repository's pinned version. Returns its exit code, or None when this
    process should run the command itself."""
    if os.environ.get(ENV_HANDOFF) or os.environ.get(ENV_NO_HANDOFF):
        return None
    if argv and argv[0] in LOCAL_COMMANDS:
        return None
    root = repository_root()
    if root is None or in_repository_environment(root):
        return None
    uv = shutil.which("uv")
    if uv is None:
        print(
            "[statera] uv not found: running this copy of statera, not the version the repository pins. "
            "Install uv: https://docs.astral.sh/uv/",
            file=sys.stderr,
        )
        return None
    env = {**os.environ, ENV_HANDOFF: "1", ENV_LAUNCHER: f"{__version__};{PROTOCOL}"}
    env.pop("VIRTUAL_ENV", None)  # another active environment would only make uv warn
    command = [uv, "run", "--quiet", "--project", str(root), *uv_groups(argv), "statera", *argv]
    try:
        return subprocess.call(command, env=env)
    except KeyboardInterrupt:
        return 130


def uv_groups(argv: list[str]) -> list[str]:
    """`uv run` options for the command: the production commands need every dependency group."""
    consumption = not argv or argv[0] in CONSUMPTION_COMMANDS or argv[0].startswith("-")
    return [] if consumption else ["--all-groups"]


def launcher() -> tuple[str, int] | None:
    """(version, protocol) of the global copy that handed off to this process, if any."""
    value = os.environ.get(ENV_LAUNCHER)
    if not value:
        return None
    version, _, protocol = value.partition(";")
    try:
        return version, int(protocol)
    except ValueError:
        return version, 0


def launcher_problem() -> str | None:
    """Message when the global copy that handed off is too old for this version."""
    info = launcher()
    if info and info[1] < MIN_LAUNCHER_PROTOCOL:
        return (
            f"the global statera ({info[0]}) is too old to hand off to statera {__version__}: "
            "run `uv tool upgrade statera`"
        )
    return None


def version_problem(required: str | None) -> str | None:
    """Message when this version does not satisfy the repository's `[project] required_version`."""
    if not required:
        return None
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        accepted = SpecifierSet(required)
    except InvalidSpecifier:
        return f"invalid [project] required_version in statera.toml: {required!r}"
    if __version__ in accepted:
        return None
    return (
        f"this repository requires statera {required} (statera.toml, [project] required_version), "
        f"but this is statera {__version__}. Run `uv sync` and use `statera` or `uv run statera`, "
        "which run the version the repository pins."
    )
