"""Catálogo de consultas recorrentes (edital, item 13), parametrizadas e testadas.

Uma consulta é uma pasta em `queries/<tema>/<slug>/` (transversal) ou em
`products/<p>/queries/<slug>/` (do produto), com:

    query.yaml       metadados: os cinco eixos, parâmetros, regras, limitações,
                        classificação da saída, responsável, situação, versão
    <slug_com_sublinhado>.sql
                        uma *analysis* do dbt: usa ref() (ganha linhagem e documentação) e
                        var('<parametro>', <padrão>) para os parâmetros; não vira tabela
    test.yaml          casos com dados sintéticos de entrada e saída esperada

Execução: os parâmetros são validados pelo TIPO declarado (nunca concatenados sem validação),
`ref()` é resolvido pelo manifest do dbt, e a saída passa pelo controle de divulgação declarado
em `classificacao_saida` antes de ser gravada com um Data Package de procedência.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import jinja2
import numpy as np
import pandas as pd

from statera.edicao import agora
from statera.process import dbt
from statera.projeto import Projeto, atual, ler_yaml

VALIDADORES = {
    "period": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "ibge-code": re.compile(r"^\d{7}$"),
    "uf": re.compile(r"^[A-Z]{2}$"),
    "integer": re.compile(r"^-?\d+$"),
    "short-text": re.compile(r"^[\w\s.,-]{1,60}$"),
    "cut": re.compile(r"^(latest|\d{4}-\d{2}-\d{2})$"),  # data de corte, ou "latest" (o último liberado)
}


class QueryError(ValueError):
    """Parâmetro inválido, consulta inexistente ou resultado divergente do esperado."""


@dataclass
class Query:
    pasta: Path
    meta: dict

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def title(self) -> str:
        return self.meta["title"]

    @property
    def dbt_name(self) -> str:
        return self.pasta.name.replace("-", "_")

    @property
    def sql(self) -> Path:
        return self.pasta / f"{self.dbt_name}.sql"

    @property
    def test_file(self) -> Path:
        return self.pasta / "test.yaml"


def load(projeto: Projeto | None = None) -> list[Query]:
    projeto = projeto or atual()
    return sorted(
        (
            Query(c.parent, ler_yaml(c))
            for base in projeto.pastas_consultas()
            if base.is_dir()
            for c in base.rglob("query.yaml")
        ),
        key=lambda c: c.id,
    )


def get(identificador: str, projeto: Projeto | None = None) -> Query:
    for c in load(projeto):
        if identificador in (c.id, c.id.rsplit(".", 1)[-1], c.pasta.name):
            return c
    raise QueryError(f"Query '{identificador}' não encontrada")


def validate_parameters(consulta: Query, valores: dict) -> dict:
    """Valores finais dos parâmetros, validados pelo tipo declarado."""
    declarados = {p["name"]: p for p in consulta.meta.get("parameters", [])}
    if extras := set(valores) - set(declarados):
        raise QueryError(f"Parâmetros não declarados: {sorted(extras)}")
    finais = {}
    for nome, p in declarados.items():
        valor = valores.get(nome, p.get("default"))
        if valor is None:
            if p.get("required"):
                raise QueryError(f"Parâmetro obrigatório ausente: {nome}")
            continue
        valor = str(valor)
        if p["type"] == "date":
            date.fromisoformat(valor)
        elif not VALIDADORES[p["type"]].match(valor):
            raise QueryError(f"Parâmetro {nome}={valor!r} não é do tipo {p['type']}")
        finais[nome] = valor
    return finais


def render(consulta: Query, parametros: dict, relacoes: dict[str, str]) -> str:
    """SQL final: ref() -> relação no motor; var() -> parâmetro já validado."""

    def ref(nome: str) -> str:
        if nome not in relacoes:
            raise QueryError(f"{consulta.id}: ref('{nome}') sem relação conhecida")
        return relacoes[nome]

    def var(nome: str, padrao=None):
        return parametros.get(nome, padrao)

    # SQL do repositório (revisado por PR) com parâmetros validados por tipo; não é HTML.
    ambiente = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    return ambiente.from_string(consulta.sql.read_text(encoding="utf-8")).render(ref=ref, var=var)


def relacoes_do_manifest(projeto: Projeto | None = None) -> dict[str, str]:
    manifesto = dbt.manifesto(gerar=False, projeto=projeto)
    return {
        n["name"]: f"{n['schema']}.{n.get('alias') or n['name']}"
        for n in manifesto["nodes"].values()
        if n["resource_type"] in ("model", "seed")
    }


def apply_control(df: pd.DataFrame, controle: dict | None) -> tuple[pd.DataFrame, int]:
    """Controle de divulgação na saída da consulta (mesma regra da publicação)."""
    from statera.analyse.controle_divulgacao import aplicar

    resultado = aplicar(df, controle)
    return resultado.dados, resultado.celulas_suprimidas


def run(identificador: str, valores: dict | None = None, saida: Path | None = None, projeto: Projeto | None = None):
    """Executa no motor. Com `saida`, grava resultado.csv e datapackage.json de procedência."""
    projeto = projeto or atual()
    consulta = get(identificador, projeto)
    parametros = validate_parameters(consulta, valores or {})
    sql = render(consulta, parametros, relacoes_do_manifest(projeto))
    con = duckdb.connect(str(projeto.warehouse), read_only=True)
    try:
        df = con.execute(sql).df()
    finally:
        con.close()
    df, suprimidas = apply_control(df, consulta.meta["output_classification"].get("disclosureControl"))
    procedencia = {
        "query": consulta.id,
        "version": consulta.meta["version"],
        "parameters": parametros,
        "executed_at": agora(),
        "suppressed_cells": suprimidas,
        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
    }
    df.attrs["procedencia"] = procedencia
    if saida:
        _gravar(df, consulta, procedencia, saida)
    return df


def _gravar(df: pd.DataFrame, consulta: Query, procedencia: dict, saida: Path) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    arquivo = saida / "resultado.csv"
    df.to_csv(arquivo, index=False)
    pacote = {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": consulta.id.rsplit(".", 1)[-1],
        "title": consulta.title,
        "resources": [
            {
                "name": "resultado",
                "path": arquivo.name,
                "format": "csv",
                "hash": f"sha256:{hashlib.sha256(arquivo.read_bytes()).hexdigest()}",
            }
        ],
        "x-statera": procedencia,
    }
    if autores := consulta.meta.get("authors"):
        pacote["contributors"] = [
            {"title": a["name"], "roles": ["author"], **({"organization": a["unit"]} if a.get("unit") else {})}
            for a in autores
        ]
    (saida / "datapackage.json").write_text(json.dumps(pacote, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Testes (dados sintéticos, sem o motor): mesmo princípio dos unit tests do dbt
# ---------------------------------------------------------------------------


def _valor(v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "None"
    if isinstance(v, bool | np.bool_):
        return str(bool(v))
    if isinstance(v, int | float) or hasattr(v, "item"):
        numero = float(v)
        return str(int(numero)) if numero.is_integer() else f"{numero:.6g}"
    return str(v)


def _normalizar(df: pd.DataFrame, colunas: list[str]) -> list[tuple]:
    linhas = df[colunas].astype(object).values.tolist()
    return sorted(tuple(_valor(v) for v in linha) for linha in linhas)


def test(consulta: Query) -> list[str]:
    """Roda os casos de test.yaml num DuckDB em memória. Devolve as falhas (vazio = ok)."""
    if not consulta.test_file.exists():
        return [f"{consulta.id}: sem test.yaml"]
    falhas = []
    for caso in ler_yaml(consulta.test_file)["cases"]:
        con = duckdb.connect()
        try:
            relacoes = {}
            for nome, linhas in caso["inputs"].items():
                con.register(f"_df_{nome}", pd.DataFrame(linhas))
                con.execute(f"CREATE TABLE t_{nome} AS SELECT * FROM _df_{nome}")
                relacoes[nome] = f"t_{nome}"
            sql = render(consulta, validate_parameters(consulta, caso["parameters"]), relacoes)
            obtido, _ = apply_control(
                con.execute(sql).df(), consulta.meta["output_classification"].get("disclosureControl")
            )
        except (duckdb.Error, QueryError, KeyError) as erro:
            falhas.append(f"{consulta.id} [{caso['id']}]: erro ao executar: {erro}")
            continue
        finally:
            con.close()
        esperado = pd.DataFrame(caso["expected"])
        colunas = list(esperado.columns)
        if missing := [c for c in colunas if c not in obtido.columns]:
            falhas.append(f"{consulta.id} [{caso['id']}]: colunas ausentes no resultado: {missing}")
        elif _normalizar(obtido, colunas) != _normalizar(esperado, colunas):
            falhas.append(
                f"{consulta.id} [{caso['id']}]: resultado difere do esperado\n{obtido[colunas].to_string(index=False)}"
            )
    return falhas
