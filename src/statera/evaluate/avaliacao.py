"""Avaliação de cada edição (GSBPM 8.1 Coletar insumos, 8.2 Conduzir a avaliação).

Os indicadores são DECLARADOS em `<produto>/8-evaluate/indicators.yaml`; nenhum exige código do
produto. Cada um diz como é calculado:

    calculo: {funcao: <nome>, ...parâmetros}   função genérica desta plataforma (FUNCOES)
    calculo: {sql: <consulta>}                 SQL no motor, com {{ competencia }}, {{ data_corte }}
                                               e {{ publicado('<porta>') }} (CSV publicado da edição)

O resultado vai para `<produto>/8-evaluate/data/<competência>/avaliacao.{json,md}` e, quando a
edição é publicada, para o registro de edições, ao lado do Data Package. O plano de ação (8.3)
fica em indicators.yaml: muda quando a equipe decide uma ação, não a cada edição.
"""

from __future__ import annotations

import calendar
import json
import operator
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import jinja2
import pandas as pd

from statera.design import bpmn
from statera.disseminate import registro
from statera.edicao import Edicao, ler_jsonl
from statera.projeto import PACOTE, Unidade

OPERADORES = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
FORMATOS_ABERTOS = {"csv", "json", "md"}


def indicadores(unidade: Unidade) -> dict:
    return unidade.ler("8-evaluate/indicators.yaml") or {"indicators": []}


def _fim_da_competencia(competencia: str) -> date:
    ano, mes = map(int, competencia.split("-"))
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def _processo(unidade: Unidade) -> bpmn.Processo | None:
    caminho = unidade.processo_to_be.with_suffix(".bpmn")
    return bpmn.ler(caminho) if caminho.exists() else None


def _datapackage(ed: Edicao) -> dict:
    caminho = ed.pasta_dados(7) / "datapackage.json"
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {"resources": []}


# ---------------------------------------------------------------------------
# Funções genéricas (calculo: {funcao: ...})
# ---------------------------------------------------------------------------


def defasagem_divulgacao(ed: Edicao, _: dict) -> float | None:
    calendario = {e["period"]: e for e in ed.unidade.ler("1-specify/calendar.yaml")["editions"]}
    previsto = calendario.get(ed.competencia, {}).get("release")
    return (date.fromisoformat(previsto) - _fim_da_competencia(ed.competencia)).days if previsto else None


def defasagem_data_corte(ed: Edicao, _: dict) -> float:
    return (date.fromisoformat(ed.data_corte) - _fim_da_competencia(ed.competencia)).days


def maior_revisao_relativa(ed: Edicao, params: dict) -> float:
    linhas = (ed.revisoes or {}).get(params["control"]) or []
    return round(100 * max((r["revisao_relativa"] for r in linhas), default=0.0), 2)


def falhas_em_verificacoes(ed: Edicao, _: dict) -> float:
    return ed.falhas_nao_bloqueantes + ed.resultados_dbt.get("testes_com_falha", 0)


def verificacoes_executadas(ed: Edicao, _: dict) -> float:
    return ed.verificacoes + ed.resultados_dbt.get("tests", 0)


def arquivos_em_formato_aberto(ed: Edicao, _: dict) -> float | None:
    formatos = [r["format"] for r in _datapackage(ed)["resources"]] + ["json", "json"]  # datapackage e ckan
    return round(100 * sum(f in FORMATOS_ABERTOS for f in formatos) / len(formatos), 1)


def duracao_automatica_relativa(ed: Edicao, _: dict) -> float | None:
    processo = _processo(ed.unidade)
    linhas = [
        r
        for r in ler_jsonl(ed.unidade.pasta_execucoes / "execucoes.jsonl")
        if r["period"] == ed.competencia
        and r["cutDate"] == ed.data_corte
        and r["status"] == "ok"
        and r.get("duracao_s") is not None
    ]
    if processo is None or not linhas:
        return None
    observado = pd.DataFrame(linhas).groupby("tarefa_bpmn")["duracao_s"].last().sum()
    modelado = bpmn.resumo(processo)["automatic_h"] * 3600
    return round(100 * observado / modelado, 1) if modelado else None


def horas_manuais_modeladas(ed: Edicao, _: dict) -> float | None:
    processo = _processo(ed.unidade)
    return round(bpmn.resumo(processo)["manual_h"], 2) if processo else None


FUNCOES: dict[str, Callable[[Edicao, dict], float | None]] = {
    f.__name__: f
    for f in (
        defasagem_divulgacao,
        defasagem_data_corte,
        maior_revisao_relativa,
        falhas_em_verificacoes,
        verificacoes_executadas,
        arquivos_em_formato_aberto,
        duracao_automatica_relativa,
        horas_manuais_modeladas,
    )
}


# ---------------------------------------------------------------------------
# Indicadores em SQL (calculo: {sql: ...})
# ---------------------------------------------------------------------------


def renderizar_sql(sql: str, competencia: str, data_corte: str, publicado: Callable[[str], str]) -> str:
    # SQL do próprio repositório (revisado por PR), não HTML: escapar corromperia a consulta.
    return (
        jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
        .from_string(sql)
        .render(competencia=competencia, data_corte=data_corte, publicado=publicado)
    )


def _sql(ed: Edicao, sql: str) -> float | None:
    def publicado(porta: str) -> str:
        nome = registro.serie(ed.unidade)["files_per_edition"]["tables"].format(porta=porta)
        return f"read_csv_auto('{ed.pasta_dados(7) / nome}')"

    con = duckdb.connect(str(ed.projeto.warehouse), read_only=True)
    try:
        linha = con.execute(renderizar_sql(sql, ed.competencia, ed.data_corte, publicado)).fetchone()
    finally:
        con.close()
    return None if not linha or linha[0] is None else float(linha[0])


def calcular_um(ed: Edicao, definicao: dict) -> float | None:
    calculo = definicao["calculation"]
    if "sql" in calculo:
        return _sql(ed, calculo["sql"])
    return FUNCOES[calculo["function"]](ed, calculo)


def problemas_de_declaracao(unidade: Unidade) -> list[str]:
    """Indicadores sem cálculo válido (verificado no CI, sem precisar de dados)."""
    erros = []
    for d in indicadores(unidade)["indicators"]:
        calculo = d.get("calculation") or {}
        if "function" in calculo and calculo["function"] not in FUNCOES:
            erros.append(f"{d['id']}: função '{calculo['function']}' não existe na plataforma ({sorted(FUNCOES)})")
        elif "sql" in calculo:
            try:
                renderizar_sql(calculo["sql"], "2000-01", "2000-02-20", lambda porta: f"tabela_{porta}")
            except jinja2.TemplateError as erro:
                erros.append(f"{d['id']}: SQL não renderiza: {erro}")
        elif not calculo:
            erros.append(f"{d['id']}: sem `calculo`")
    return erros


# ---------------------------------------------------------------------------
# Avaliação (GSBPM 8.2) e relatório
# ---------------------------------------------------------------------------


def _formatar(valor, unidade: str) -> str:
    if valor is None:
        return "n/d"
    numero = f"{valor:,.2f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{numero} {unidade}".strip()


def calcular(ed: Edicao) -> list[dict]:
    saida = []
    for definicao in indicadores(ed.unidade)["indicators"]:
        valor = calcular_um(ed, definicao)
        alerta = definicao.get("alert")
        em_alerta = bool(alerta and valor is not None and OPERADORES[alerta["operator"]](valor, alerta["value"]))
        saida.append(
            {
                **{k: v for k, v in definicao.items() if k != "calculation"},
                "value": valor,
                "valor_formatado": _formatar(valor, definicao["unit"]),
                "alerta_texto": f"{alerta['operator']} {alerta['value']}" if alerta else "—",
                "em_alerta": em_alerta,
            }
        )
    return saida


def avaliar(ed: Edicao) -> Path:
    """Calcula os indicadores e grava JSON e relatório em <produto>/8-evaluate/data/<competência>/."""
    resultados = calcular(ed)
    contexto = {
        "product": ed.produto,
        "period": ed.competencia,
        "cut_date": ed.data_corte,
        "version": ed.publicacao.get("version", ""),
        "run": ed.run_id,
        "indicators": resultados,
        "alertas": [r for r in resultados if r["em_alerta"]],
        "acoes": [a for a in indicadores(ed.unidade).get("action_plan", []) if a["status"] != "done"],
        "synthetic": ed.projeto.sintetico,
    }
    pasta = ed.pasta_dados(8)
    pasta.mkdir(parents=True, exist_ok=True)
    dados = {k: v for k, v in contexto.items() if k != "acoes"}
    (pasta / "avaliacao.json").write_text(
        json.dumps(dados, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    ambiente = jinja2.Environment(
        loader=jinja2.FileSystemLoader(PACOTE / "evaluate"),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=jinja2.select_autoescape(["html"], default=False),
    )
    relatorio = pasta / "avaliacao.md"
    relatorio.write_text(ambiente.get_template("modelo-avaliacao.md.j2").render(**contexto), encoding="utf-8")
    return relatorio


def historico(unidade: Unidade) -> pd.DataFrame:
    """Uma linha por edição PUBLICADA e avaliada, uma coluna por indicador: base para tendências."""
    linhas = []
    for arquivo in sorted(registro.pasta_avaliacoes(unidade).glob("*.json")):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        linha = {"period": dados["period"]}
        linha.update({i["id"]: i["valor_formatado"] + (" ⚠" if i["em_alerta"] else "") for i in dados["indicators"]})
        linhas.append(linha)
    return pd.DataFrame(linhas)
