"""`statera init`: a new data repository starts out valid, and so does a new product."""

import os
import tomllib
from pathlib import Path

import pytest

from statera import cli, validation


@pytest.fixture
def new_repo(tmp_path: Path, monkeypatch):
    destination = tmp_path / "example-stats"
    assert cli.main([
        "init", str(destination), "--organization", "Example statistics", "--namespace", "org.example", "--no-git",
    ]) == 0  # fmt: skip
    monkeypatch.chdir(destination / "content")
    return destination


def test_init_creates_the_layout(new_repo: Path):
    for rel in (
        "README.md",
        ".github/workflows/ci.yml",
        ".gitattributes",
        ".vscode/tasks.json",
        "pyproject.toml",
        "content/statera.toml",
        "publications/README.md",
        "docs/README.md",
    ):
        assert (new_repo / rel).is_file(), rel
    toml = tomllib.loads((new_repo / "content/statera.toml").read_text())
    assert toml["project"]["namespace"] == "org.example"
    assert toml["registry"]["editions"] == "../publications"
    answers = (new_repo / ".copier-answers.yml").read_text()
    assert str(Path(cli.__file__).parent) not in answers  # no absolute path of whoever created it
    deps = tomllib.loads((new_repo / "pyproject.toml").read_text())["tool"]["uv"]["sources"]["statera"]
    assert deps["tag"].startswith("v")
    assert toml["project"]["required_version"] == f"~={deps['tag'][1:]}"
    assert "eol=lf" in (new_repo / ".gitattributes").read_text()
    assert not (new_repo / "Makefile").exists() and not (new_repo / "content/pyproject.toml").exists()


def test_new_repository_and_product_validate(new_repo: Path):
    assert cli.main(["new-product", "my-statistic"]) == 0
    assert cli.main(["sync"]) == 0
    failures = [a for a in validation.collect_findings() if not a.ok]
    assert not failures, failures


def test_init_refuses_folder_with_content(tmp_path: Path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "something.txt").write_text("")
    assert cli.main(["init", str(tmp_path / "x"), "--no-git"]) == 1
    assert os.listdir(tmp_path / "x") == ["something.txt"]
