"""`statera init`: um repositório de dados novo nasce válido, e um produto novo também."""

import os
import tomllib
from pathlib import Path

import pytest

from statera import cli, validacao


@pytest.fixture
def repo_novo(tmp_path: Path, monkeypatch):
    destino = tmp_path / "estat-exemplo"
    assert cli.main([
        "init", str(destino), "--organization", "Estatísticas de exemplo", "--namespace", "br.gov.exemplo", "--no-git",
    ]) == 0  # fmt: skip
    monkeypatch.chdir(destino / "content")
    return destino


def test_init_cria_o_layout(repo_novo: Path):
    for rel in (
        "README.md",
        "Makefile",
        ".github/workflows/ci.yml",
        "estat-exemplo.code-workspace",
        "content/statera.toml",
        "content/pyproject.toml",
        "publications/README.md",
        "docs/README.md",
    ):
        assert (repo_novo / rel).is_file(), rel
    toml = tomllib.loads((repo_novo / "content/statera.toml").read_text())
    assert toml["project"]["namespace"] == "br.gov.exemplo"
    assert toml["registry"]["editions"] == "../publications"
    respostas = (repo_novo / ".copier-answers.yml").read_text()
    assert str(Path(cli.__file__).parent) not in respostas  # sem caminho absoluto de quem criou
    deps = tomllib.loads((repo_novo / "content/pyproject.toml").read_text())["tool"]["uv"]["sources"]["statera"]
    assert deps["tag"].startswith("v")


def test_repositorio_e_produto_novos_validam(repo_novo: Path):
    assert cli.main(["new-product", "minha-estatistica"]) == 0
    assert cli.main(["sync"]) == 0
    falhas = [a for a in validacao.coletar() if not a.ok]
    assert not falhas, falhas


def test_init_recusa_pasta_com_conteudo(tmp_path: Path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "algo.txt").write_text("")
    assert cli.main(["init", str(tmp_path / "x"), "--no-git"]) == 1
    assert os.listdir(tmp_path / "x") == ["algo.txt"]
