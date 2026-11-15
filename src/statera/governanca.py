"""Papéis, responsáveis e geração do CODEOWNERS.

`governance/roles.yaml` lista os papéis (com unidade SIORG e equipe na plataforma git).
`governance/shared-owners.yaml` e `<unidade>/1-specify/owners.yaml` dizem
quem responde por quais caminhos. Nos arquivos das unidades, os caminhos são relativos à pasta da
unidade; a plataforma os prefixa ao gerar o `.github/CODEOWNERS`, que é a única saída.
"""

from __future__ import annotations

from pathlib import Path

from statera import gerado
from statera.projeto import Projeto, atual, ler_yaml


def papeis(projeto: Projeto | None = None) -> dict[str, dict]:
    dados = ler_yaml((projeto or atual()).governanca / "roles.yaml")
    return {p["id"]: p for p in dados["roles"]}


def responsaveis(projeto: Projeto | None = None) -> list[tuple[Path, dict]]:
    """Transversal primeiro: no CODEOWNERS, a última regra que casa prevalece."""
    return [(c, ler_yaml(c)) for c in (projeto or atual()).responsaveis()]


def _padrao_absoluto(projeto: Projeto, arquivo: Path, padrao: str) -> str:
    """Padrão relativo à raiz do repositório git (o conteúdo pode estar numa subpasta, como content/)."""
    if padrao == "*":
        return padrao
    unidade = projeto.unidade_de(arquivo)
    base = f"/{unidade.relativo}/{padrao.lstrip('/')}" if unidade else padrao
    prefixo = prefixo_git(projeto)
    return (f"/{prefixo}{base}" if prefixo and base.startswith("/") else base).replace("//", "/")


def gerar_codeowners(projeto: Projeto | None = None) -> str:
    projeto = projeto or atual()
    mapa = papeis(projeto)
    aviso = gerado.cabecalho(
        "statera codeowners",
        "governance/shared-owners.yaml, governance/roles.yaml e <unidade>/1-specify/owners.yaml",
        nota="Equipes fictícias (@org-exemplo) nesta demonstração." if projeto.sintetico else None,
    )
    linhas = [*aviso.rstrip("\n").split("\n"), ""]
    for caminho, dados in responsaveis(projeto):
        linhas.append(f"# --- {projeto.rel(caminho)}")
        for regra in dados["paths"]:
            # papel inexistente: a validação (referências) aponta; aqui só não derruba a geração
            equipes = " ".join(mapa[p]["git_team"] for p in regra["roles"] if p in mapa) or "# papel desconhecido"
            linhas.append(f"{_padrao_absoluto(projeto, caminho, regra['pattern']):<60} {equipes}")
        linhas.append("")
    return "\n".join(linhas)


def raiz_git(projeto: Projeto) -> Path:
    """A raiz do repositório que contém o conteúdo (o GitHub só lê o CODEOWNERS de lá): a declarada em
    `[project] repository_root` (relativa ao statera.toml; `statera init` grava ".."); senão, a raiz do
    git; senão, a própria raiz do conteúdo."""
    declarada = projeto._secao("project").get("repository_root")
    if declarada:
        return (projeto.raiz / declarada).resolve()
    for pasta in (projeto.raiz, *projeto.raiz.parents):
        if (pasta / ".git").exists():
            return pasta
    return projeto.raiz


def prefixo_git(projeto: Projeto) -> str:
    """Caminho do conteúdo dentro do repositório git ('' quando o conteúdo é a raiz; senão, ex.: 'content')."""
    rel = projeto.raiz.relative_to(raiz_git(projeto)).as_posix()
    return "" if rel == "." else rel


def arquivo_codeowners(projeto: Projeto | None = None) -> Path:
    projeto = projeto or atual()
    return raiz_git(projeto) / ".github" / "CODEOWNERS"


def escrever_codeowners(projeto: Projeto | None = None) -> Path:
    destino = arquivo_codeowners(projeto)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar_codeowners(projeto), encoding="utf-8")
    return destino


def codeowners_atualizado(projeto: Projeto | None = None) -> bool:
    destino = arquivo_codeowners(projeto)
    return destino.exists() and destino.read_text(encoding="utf-8") == gerar_codeowners(projeto)


def papeis_referenciados(dados: dict) -> set[str]:
    usados = {
        dados["curator"],
        dados["technical_owner"],
        dados["data_protection_officer"],
        *dados.get("deputies", []),
        *dados["required_reviewers"],
    }
    for regra in dados["paths"]:
        usados.update(regra["roles"])
    return usados


def nome_do_papel(papel: str, projeto: Projeto | None = None) -> str:
    return papeis(projeto).get(papel, {}).get("name", papel)
