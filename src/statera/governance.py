"""Roles, owners and CODEOWNERS generation.

`governance/roles.yaml` lists the roles (with SIORG organizational unit and team on the git platform).
`governance/shared-owners.yaml` and `<unit>/1-specify/owners.yaml` say
who answers for which paths. In the units' files, paths are relative to the unit folder; the
framework prefixes them when generating `.github/CODEOWNERS`, which is the only output.
"""

from __future__ import annotations

from pathlib import Path

from statera import generated
from statera.project import Project, current, read_yaml


def roles(project: Project | None = None) -> dict[str, dict]:
    data = read_yaml((project or current()).governance / "roles.yaml")
    return {p["id"]: p for p in data["roles"]}


def owners(project: Project | None = None) -> list[tuple[Path, dict]]:
    """Shared owners first: in CODEOWNERS, the last matching rule wins."""
    return [(c, read_yaml(c)) for c in (project or current()).owners()]


def _absolute_pattern(project: Project, file: Path, default: str) -> str:
    """Pattern relative to the git repository root (the content may be in a subfolder, such as content/)."""
    if default == "*":
        return default
    unit = project.unit_of(file)
    base = f"/{unit.relative}/{default.lstrip('/')}" if unit else default
    prefix = git_prefix(project)
    return (f"/{prefix}{base}" if prefix and base.startswith("/") else base).replace("//", "/")


def generate_codeowners(project: Project | None = None) -> str:
    project = project or current()
    mapping = roles(project)
    notice = generated.header(
        "statera codeowners",
        "governance/shared-owners.yaml, governance/roles.yaml and <unit>/1-specify/owners.yaml",
        note="Fictional teams (@org-example) in this demo." if project.synthetic else None,
    )
    lines = [*notice.rstrip("\n").split("\n"), ""]
    for path, data in owners(project):
        lines.append(f"# --- {project.rel(path)}")
        for rule in data["paths"]:
            # nonexistent role: validation (references) reports it; here we just don't break generation
            teams = " ".join(mapping[p]["git_team"] for p in rule["roles"] if p in mapping) or "# unknown role"
            lines.append(f"{_absolute_pattern(project, path, rule['pattern']):<60} {teams}")
        lines.append("")
    return "\n".join(lines)


def git_root(project: Project) -> Path:
    """The root of the repository holding the content (GitHub only reads CODEOWNERS from there): the one
    declared in `[project] repository_root` (relative to statera.toml; `statera init` writes "..");
    otherwise the git root; otherwise the content root itself."""
    declared = project._section("project").get("repository_root")
    if declared:
        return (project.root / declared).resolve()
    for folder in (project.root, *project.root.parents):
        if (folder / ".git").exists():
            return folder
    return project.root


def git_prefix(project: Project) -> str:
    """Path of the content inside the git repository ('' when the content is the root; otherwise e.g. 'content')."""
    rel = project.root.relative_to(git_root(project)).as_posix()
    return "" if rel == "." else rel


def codeowners_file(project: Project | None = None) -> Path:
    project = project or current()
    return git_root(project) / ".github" / "CODEOWNERS"


def write_codeowners(project: Project | None = None) -> Path:
    destination = codeowners_file(project)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generate_codeowners(project), encoding="utf-8")
    return destination


def codeowners_up_to_date(project: Project | None = None) -> bool:
    destination = codeowners_file(project)
    return destination.exists() and destination.read_text(encoding="utf-8") == generate_codeowners(project)


def referenced_roles(data: dict) -> set[str]:
    in_use = {
        data["curator"],
        data["technical_owner"],
        data["data_protection_officer"],
        *data.get("deputies", []),
        *data["required_reviewers"],
    }
    for rule in data["paths"]:
        in_use.update(rule["roles"])
    return in_use


def role_name(role: str, project: Project | None = None) -> str:
    return roles(project).get(role, {}).get("name", role)


def unit_owner(unit) -> dict:
    """The role that answers for a unit (`technical_owner` in its owners.yaml, else the curator), with its
    name and git team: who to notify when something the unit reads changes."""
    path = unit.phase(1) / "owners.yaml"
    data = read_yaml(path) if path.exists() else {}
    role = data.get("technical_owner") or data.get("curator")
    if not role:
        return {"id": "", "name": "(no owner declared)", "git_team": ""}
    found = roles(unit.project).get(role, {})
    return {"id": role, "name": found.get("name", role), "git_team": found.get("git_team", "")}
