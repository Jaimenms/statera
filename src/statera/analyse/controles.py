"""Controles declarativos da edição (GSBPM 6.2 Validar resultados).

O produto declara os controles em `6-analyse/controls.yaml`; os TIPOS de controle são da
plataforma. Os controles de identidade da edição corrente ficam no dbt (testes singulares em
`6-analyse/dbt-tests/`); aqui ficam os que comparam com a edição anterior e com o histórico.

Tipos disponíveis:
    revisao-entre-edicoes  diferença, por chave, entre a edição corrente e a anterior publicada
                           (porta, chave, medidas, base_relativa, limite_alerta, limite_erro)
    valor-atipico          valor da competência corrente frente à média do histórico
                           (porta, medida, fator, severidade)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import duckdb
import pandas as pd

from statera.analyse.verificacao import Resultado


@dataclass
class Insumos:
    competencia: str
    atual: Callable[[str], pd.DataFrame]  # porta -> tabela no motor
    anterior: Callable[[str], pd.DataFrame | None]  # porta -> tabela publicada na edição anterior
    coluna_periodo: str = "period"  # [conventions] period_column do statera.toml


def revisoes(atual: pd.DataFrame, anterior: pd.DataFrame, chave: list[str], medidas: list[str], base: str):
    """Diferença, por chave, entre a edição corrente e a anterior."""
    juntos = atual[chave + medidas].merge(anterior[chave + medidas], on=chave, suffixes=("", "_anterior"))
    for col in medidas:
        juntos[f"revisao_{col}"] = juntos[col] - juntos[f"{col}_anterior"]
    referencia = juntos[f"{base}_anterior"].where(juntos[f"{base}_anterior"] > 0)
    juntos["revisao_relativa"] = (juntos[f"revisao_{base}"].abs() / referencia).fillna(0.0)
    return juntos


def _revisao(c: dict, ins: Insumos) -> tuple[list[Resultado], list[dict] | None]:
    anterior = ins.anterior(c["port"])
    if anterior is None or anterior.empty:
        return [Resultado("control", f"{c['id']} {c['name']}", True, "sem edição anterior para comparar", "info")], None
    tabela = revisoes(ins.atual(c["port"]), anterior, c["key"], c["measures"], c["relative_base"])
    resultados = []
    for linha in tabela.to_dict("records"):
        rel = linha["revisao_relativa"]
        ok = rel <= c["warning_threshold"]
        severidade = "info" if ok else ("error" if rel > c["error_threshold"] else "warning")
        chave = "/".join(str(linha[k]) for k in c["key"])
        base = c["relative_base"]
        detalhe = f"{base} {linha[f'{base}_anterior']} -> {linha[base]} ({rel:.1%})"
        resultados.append(Resultado("control", f"{c['id']} revisão {chave}", ok, detalhe, severidade))
    return resultados, tabela.to_dict("records")


def _atipico(c: dict, ins: Insumos) -> tuple[list[Resultado], None]:
    atual = ins.atual(c["port"])
    historico = atual.loc[atual[ins.coluna_periodo] < ins.competencia, c["measure"]].abs()
    corrente = atual.loc[atual[ins.coluna_periodo] == ins.competencia, c["measure"]]
    nome = f"{c['id']} {c['name']}"
    if historico.empty or corrente.empty:
        return [Resultado("control", nome, True, "histórico insuficiente", "info")], None
    media, valor = historico.mean(), float(corrente.iloc[0])
    ok = abs(valor) <= c["factor"] * max(media, 1)
    detalhe = f"{c['measure']}={valor:.0f}; média |{c['measure']}| anterior={media:.0f}; fator {c['factor']}"
    return [Resultado("control", nome, ok, detalhe, c.get("severity", "warning"))], None


TIPOS: dict[str, Callable] = {"revision-between-editions": _revisao, "outlier": _atipico}


def executar(definicoes: list[dict], insumos: Insumos) -> tuple[list[Resultado], dict[str, list[dict]]]:
    """Roda os controles declarados. Devolve os resultados e as tabelas de revisão por controle."""
    resultados, tabelas = [], {}
    for definicao in definicoes:
        try:
            saida, tabela = TIPOS[definicao["type"]](definicao, insumos)
        except (duckdb.Error, KeyError) as erro:  # ex.: tabela não construída porque o dbt falhou antes
            nome = f"{definicao['id']} {definicao['name']}"
            saida, tabela = [Resultado("control", nome, False, f"não executado: {str(erro).splitlines()[0]}")], None
        resultados += saida
        if tabela is not None:
            tabelas[definicao["id"]] = tabela
    return resultados, tabelas
