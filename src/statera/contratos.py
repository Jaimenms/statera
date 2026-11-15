"""Catálogo de contratos ODCS v3.1 e o perfil statera sobre eles.

Os contratos ficam com a unidade e a fase que PRODUZEM o conjunto de dados:

    products/<fonte>/4-collect/contracts/        bronze (extrações; produto interno de fonte)
    products/<agregado>/5-process/contracts/   silver (entidades canônicas; produto interno agregado)
    products/<consumo>/5-process/contracts/    gold (tabelas de divulgação)
    references/<r>/                             ref (referências oficiais)

Perfil statera (especificação v0, `04-perfis.md`): além do esquema oficial do ODCS, todo
contrato declara em `customProperties`:

    camada          bronze | silver | gold | ref
    urn             <espaço de nomes>.<unidade>.<camada>.<tabela com hífens>
    classificacao   objeto com as quatro dimensões (ver classificacao.py)
    dbtSource | dbtModel | dbtSeed   o nó do dbt que materializa a tabela
"""

from __future__ import annotations

from pathlib import Path

from statera.projeto import Projeto, Unidade, atual, ler_yaml

CAMADAS = ("bronze", "silver", "gold", "ref")


def propriedade_custom(objeto: dict, nome: str, padrao=None):
    for item in objeto.get("customProperties", []) or []:
        if item.get("property") == nome:
            return item.get("value")
    return padrao


def arquivos(projeto: Projeto | None = None) -> list[Path]:
    projeto = projeto or atual()
    return [c for u in projeto.unidades for c in u.contratos()]


def todos(projeto: Projeto | None = None) -> dict[str, tuple[Path, dict]]:
    """Todos os contratos do repositório, indexados pelo id."""
    resultado = {}
    for caminho in arquivos(projeto):
        dados = ler_yaml(caminho)
        resultado[dados["id"]] = (caminho, dados)
    return resultado


def camada(contrato: dict) -> str | None:
    return propriedade_custom(contrato, "layer")


def tabela_fisica(contrato: dict) -> str:
    return contrato["schema"][0]["physicalName"]


def urn_esperado(unidade: Unidade, contrato: dict) -> str:
    tabela = tabela_fisica(contrato).split(".")[-1].replace("_", "-")
    return f"{unidade.urn}.{camada(contrato)}.{tabela}"


def por_tabela(projeto: Projeto | None = None) -> dict[str, dict]:
    """Contrato de cada tabela física ('gold.caged_saldo_uf_mensal' -> contrato)."""
    return {tabela_fisica(c): c for _, c in todos(projeto).values()}


def no_dbt(contrato: dict) -> tuple[str, str] | None:
    """('source'|'model'|'seed', nome) do nó dbt que materializa a tabela."""
    for chave, tipo in (("dbtSource", "source"), ("dbtModel", "model"), ("dbtSeed", "seed")):
        valor = propriedade_custom(contrato, chave)
        if valor:
            return tipo, valor
    return None
