"""Receitas: exemplos curtos e completos, executados no CI para não envelhecerem.

Cada receita é um arquivo em `recipes/`:
  - `.py` no formato de células (`# %%`, compatível com Jupytext e com o VS Code), que usa a
    biblioteca (`statera.consume.catalog`);
  - `.sql`, executado direto no motor (somente leitura).

O cabeçalho declara título, camadas lidas e classificação, em linhas de comentário:

    # Recipe: Série histórica de saldo por UF
    # Camadas: gold
    # Classificação: amplo · agregado
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from statera.projeto import Projeto, atual

CABECALHO = re.compile(r"^(?:#|--)\s*(Recipe|Layers|Classification|Library|Authors):\s*(.+)$")


@dataclass
class Recipe:
    caminho: Path
    meta: dict

    @property
    def title(self) -> str:
        return self.meta.get("recipe", self.caminho.stem)


def _cabecalho(caminho: Path) -> dict:
    meta = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines()[:15]:
        if achado := CABECALHO.match(linha.strip()):
            chave = achado.group(1).lower()
            meta[chave] = achado.group(2).strip()
    return meta


def load(projeto: Projeto | None = None) -> list[Recipe]:
    pasta = (projeto or atual()).receitas
    arquivos = sorted([*pasta.glob("*.py"), *pasta.glob("*.sql")]) if pasta.is_dir() else []
    return [Recipe(a, _cabecalho(a)) for a in arquivos]


def run(receita: Recipe, projeto: Projeto | None = None) -> tuple[bool, str]:
    """Executa a receita contra o motor do projeto. Devolve (sucesso, saída)."""
    projeto = projeto or atual()
    if receita.caminho.suffix == ".sql":
        con = duckdb.connect(str(projeto.warehouse), read_only=True)
        try:
            return True, con.execute(receita.caminho.read_text(encoding="utf-8")).df().to_string(index=False)
        except duckdb.Error as erro:
            return False, str(erro)
        finally:
            con.close()
    # avisos de depreciação de bibliotecas de terceiros poluem a saída; os avisos de classificação ficam
    env = {
        **os.environ,
        "STATERA_PROJETO": str(projeto.raiz),
        "PYTHONWARNINGS": "ignore::DeprecationWarning,ignore::FutureWarning",
    }
    processo = subprocess.run(
        [sys.executable, str(receita.caminho)], cwd=projeto.raiz, env=env, capture_output=True, text=True
    )
    return processo.returncode == 0, (processo.stdout + processo.stderr).strip()


def header_problems(projeto: Projeto | None = None) -> list[str]:
    return [
        f"{r.caminho.name}: cabeçalho sem {campo}"
        for r in load(projeto)
        for campo in ("recipe", "layers", "classification")
        if campo not in r.meta
    ]
