"""The global `statera` hands off to the version the data repository pins (launcher.py)."""

from pathlib import Path

import pytest

from statera import __version__, cli, launcher
from statera.project import discover_root


def _repo(root: Path, pyproject_in_content: bool = False) -> Path:
    (root / "content").mkdir(parents=True)
    (root / "content" / "statera.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (root / "publications").mkdir()
    (root / ("content" if pyproject_in_content else ".") / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return root


def test_root_is_the_folder_with_pyproject_from_any_subfolder(tmp_path: Path):
    root = _repo(tmp_path / "repo")
    for start in (root, root / "content", root / "publications"):
        assert launcher.repository_root(start) == root.resolve()
        assert discover_root(start) == (root / "content").resolve()


def test_layout_with_pyproject_in_content_still_works(tmp_path: Path):
    root = _repo(tmp_path / "old", pyproject_in_content=True)
    assert launcher.repository_root(root / "content") == (root / "content").resolve()


def test_outside_a_repository_there_is_no_root(tmp_path: Path):
    assert launcher.repository_root(tmp_path) is None


def test_no_handoff_inside_the_handed_off_process_or_for_init(tmp_path: Path, monkeypatch):
    _repo(tmp_path / "repo")
    monkeypatch.chdir(tmp_path / "repo")
    monkeypatch.setenv(launcher.ENV_HANDOFF, "1")
    assert launcher.handoff(["check"]) is None  # never twice: no loop
    monkeypatch.delenv(launcher.ENV_HANDOFF)
    assert launcher.handoff(["init", "new-repo"]) is None  # init creates a repository, it doesn't run in one


def test_handoff_runs_the_pinned_version_through_uv(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path / "repo")
    monkeypatch.chdir(root / "publications")
    monkeypatch.delenv(launcher.ENV_HANDOFF, raising=False)
    monkeypatch.delenv(launcher.ENV_NO_HANDOFF, raising=False)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda cmd, env: calls.append((cmd, env)) or 7)
    assert launcher.handoff(["check", "--all"]) == 7
    command, env = calls[0]
    assert command == ["/bin/uv", "run", "--quiet", "--project", str(root.resolve()), "statera", "check", "--all"]
    assert env[launcher.ENV_HANDOFF] == "1"
    assert env[launcher.ENV_LAUNCHER] == f"{__version__};{launcher.PROTOCOL}"


def test_required_version(monkeypatch):
    assert launcher.version_problem(None) is None
    assert launcher.version_problem(f"~={__version__}") is None
    assert "requires statera >=99" in launcher.version_problem(">=99")
    assert "invalid" in launcher.version_problem("not a version")


def test_old_global_launcher_is_asked_to_upgrade(monkeypatch):
    monkeypatch.setenv(launcher.ENV_LAUNCHER, "0.0.1;0")
    assert "uv tool upgrade statera" in launcher.launcher_problem()
    monkeypatch.setenv(launcher.ENV_LAUNCHER, f"{__version__};{launcher.PROTOCOL}")
    assert launcher.launcher_problem() is None


def test_commands_refuse_a_version_outside_the_required_range(tmp_path: Path, monkeypatch, capsys):
    root = _repo(tmp_path / "repo")
    toml = root / "content" / "statera.toml"
    toml.write_text('[project]\nname = "x"\nrequired_version = ">=99"\n', encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("STATERA_PROJECT", raising=False)
    assert cli.main(["cuts"]) == 2
    assert "uv sync" in capsys.readouterr().err


@pytest.mark.parametrize("command", [["--version"]])
def test_version_flag(command, capsys):
    with pytest.raises(SystemExit):
        cli.main(command)
    assert __version__ in capsys.readouterr().out
