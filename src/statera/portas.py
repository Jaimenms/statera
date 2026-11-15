"""Portas de saída de um produto (ODPS) e as tabelas que elas publicam (contratos ODCS)."""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from statera import contratos
from statera.projeto import Unidade


def odps(unidade: Unidade) -> dict:
    return unidade.ler("1-specify/product.odps.yaml")


def portas_de_saida(unidade: Unidade) -> list[dict]:
    return odps(unidade).get("outputPorts", [])


def contrato_da_porta(unidade: Unidade, porta: str) -> dict:
    todos = contratos.todos(unidade.projeto)
    for p in portas_de_saida(unidade):
        if p["name"] == porta:
            return todos[p["contractId"]][1]
    raise KeyError(f"{unidade.nome}: porta de saída '{porta}' não existe no product.odps.yaml")


def chave(contrato: dict) -> list[str]:
    props = contrato["schema"][0]["properties"]
    pk = sorted((p for p in props if p.get("primaryKey")), key=lambda p: p.get("primaryKeyPosition", 1))
    return [p["name"] for p in pk] or [props[0]["name"]]


def ler(contrato: dict, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Lê do motor as colunas do contrato, na ordem da chave."""
    objeto = contrato["schema"][0]
    colunas = ", ".join(p["name"] for p in objeto["properties"])
    return con.execute(f"SELECT {colunas} FROM {objeto['physicalName']} ORDER BY {', '.join(chave(contrato))}").df()


def ler_porta(unidade: Unidade, porta: str) -> pd.DataFrame:
    con = duckdb.connect(str(unidade.projeto.warehouse), read_only=True)
    try:
        return ler(contrato_da_porta(unidade, porta), con)
    finally:
        con.close()


def dono_do_contrato(projeto, contract_id: str) -> Unidade | None:
    todos = contratos.todos(projeto)
    return projeto.unidade_de(todos[contract_id][0]) if contract_id in todos else None


def produtos_de_entrada(unidade: Unidade) -> list[Unidade]:
    """Produtos INTERNOS de que a unidade depende (pelas portas de entrada do ODPS)."""
    vistos, saida = set(), []
    for porta in odps(unidade).get("inputPorts", []) or []:
        dono = dono_do_contrato(unidade.projeto, porta["contractId"])
        if dono and dono.interno and dono.nome not in vistos and dono.nome != unidade.nome:
            vistos.add(dono.nome)
            saida.append(dono)
    return saida


def consumidores_autorizados(unidade: Unidade) -> dict[str, str]:
    """{produto ou id de consulta: finalidade} declarados pelo produto interno.

    Cada item autoriza um produto (`product: novo-caged`) ou uma consulta recorrente transversal
    (`query: br.gov.estat-trabalho.query.x`), sempre com a finalidade (LGPD, art. 6º, I).
    As autorizações de equipes (`role`) estão em `papeis_autorizados`.
    """
    itens = unidade.propriedade("authorizedConsumers", []) or []
    return {c.get("product") or c.get("query"): c.get("purpose", "") for c in itens if not c.get("role")}


def papeis_autorizados(unidade: Unidade) -> list[dict]:
    """Equipes autorizadas a ler o produto interno diretamente (no ambiente do órgão, sem download):
    `{papel, finalidade, ate, ato}`. O acesso é da equipe, por uma finalidade e até uma data, e o ato
    diz quem autorizou (LGPD, art. 6º, I e III; Decreto nº 10.046/2019)."""
    return [c for c in unidade.propriedade("authorizedConsumers", []) or [] if c.get("role")]


def autorizacao_vigente(unidade: Unidade, papel: str, hoje: date | None = None) -> dict | None:
    hoje = hoje or date.today()
    for a in papeis_autorizados(unidade):
        if a["role"] == papel and a.get("until") and date.fromisoformat(str(a["until"])) >= hoje:
            return a
    return None


def produtos_lidos_pela_consulta(projeto, bases: list[str]) -> list[Unidade]:
    """Produtos INTERNOS donos das tabelas que uma consulta recorrente lê."""
    por_tabela = contratos.por_tabela(projeto)
    todos = contratos.todos(projeto)
    saida = []
    for base in bases:
        contrato = por_tabela.get(base)
        dono = projeto.unidade_de(todos[contrato["id"]][0]) if contrato else None
        if dono and dono.interno and dono not in saida:
            saida.append(dono)
    return saida
