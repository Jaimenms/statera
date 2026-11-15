"""Geração do pacote de uma edição (GSBPM 7.1 e 7.2), sem nada específico de produto.

Tudo é dirigido por metadados:
  - o que publicar: portas de saída do produto (ODPS);
  - de onde ler, descrição, limitações, esquema e controle de divulgação: contratos ODCS;
  - o que pode ser publicado: classificação aprovada (gate da fase 6);
  - nomes de arquivo, licença, porta principal: a série (7-disseminate/series.yaml);
  - o texto do sumário: o modelo Jinja2 da série, preenchido com as tabelas publicadas.

Saídas em `<produto>/7-disseminate/data/<competência>/`: um CSV por porta, planilha xlsx, sumário,
datapackage.json (Frictionless Data Package 2.0) e ckan.json (carga para o dados.gov.br).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import duckdb
import jinja2
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from statera import classificacao, contratos, governanca, portas
from statera.analyse.controle_divulgacao import Tabela
from statera.projeto import Unidade

MESES = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]
TIPO_FRICTIONLESS = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "date": "date",
    "timestamp": "datetime",
    "boolean": "boolean",
}
MEDIATYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
}


# ---------------------------------------------------------------------------
# Formatação pt-BR (filtros do sumário)
# ---------------------------------------------------------------------------


def milhar(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def sinal(v) -> str:
    return ("+" if int(v) > 0 else "") + milhar(v)


def pct(v) -> str:
    return f"{float(v):+.2f}".replace(".", ",") + "%"


def reais(v) -> str:
    return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def competencia_extenso(competencia: str) -> str:
    ano, mes = competencia.split("-")
    return f"{MESES[int(mes) - 1]} de {ano}"


# ---------------------------------------------------------------------------
# Carga das portas de saída
# ---------------------------------------------------------------------------


def carregar_tabelas(unidade: Unidade) -> list[Tabela]:
    todos = contratos.todos(unidade.projeto)
    con = duckdb.connect(str(unidade.projeto.warehouse), read_only=True)
    tabelas = []
    try:
        for porta in portas.portas_de_saida(unidade):
            _, contrato = todos[porta["contractId"]]
            aba = contratos.propriedade_custom(porta, "spreadsheetTab", porta["name"])[:31]
            descricao = porta.get("description", porta["name"])
            t = Tabela(porta["name"], descricao, aba, porta["contractId"], contrato, portas.ler(contrato, con))
            t.na_planilha = contratos.propriedade_custom(porta, "inSpreadsheet", True) is not False
            tabelas.append(t)
    finally:
        con.close()
    return tabelas


# ---------------------------------------------------------------------------
# Planilha
# ---------------------------------------------------------------------------

ESTILO_CABECALHO = Font(bold=True, color="FFFFFF")
FUNDO_CABECALHO = PatternFill("solid", fgColor="1F4E79")


def _formato(coluna: str, prop: dict | None) -> str | None:
    if coluna.endswith("_pct"):
        return "0.00"
    tipo = (prop or {}).get("logicalType")
    if tipo == "date":
        return "dd/mm/yyyy"
    if tipo == "number":
        return "#,##0.00"
    if tipo == "integer" and not coluna.startswith("cod_"):
        return "#,##0"
    return None


def _notas(tabelas: list[Tabela], contexto: dict) -> list[tuple]:
    linhas = [(f"{contexto['product']['name']} — {contexto['edition']['period_long']}", None)]
    if contexto["synthetic"]:
        linhas.append(("DADOS SINTÉTICOS — DEMONSTRAÇÃO. Não usar para análise.", None))
    ed = contexto["edition"]
    linhas += [
        ("Competência", ed["period"]),
        ("Data de corte da extração", ed["cut_date"]),
        ("Versão do registro", ed["version"]),
        ("Gerado em", ed["generated_at"]),
        ("Versão do código", ed["code_version"]),
        ("", None),
        ("Tabelas", None),
    ]
    for t in tabelas:
        linhas.append((t.descricao, t.contrato["description"].get("purpose", "").strip()))
        if limitacoes := t.contrato["description"].get("limitations"):
            linhas.append(("    Limitações", " ".join(limitacoes.split())))
    for t in tabelas:
        if t.controle_aplicado:
            c = t.controle
            texto = f"Suprimidas pelo controle de divulgação: {c['column']} < {c['minimum']} ({t.descricao})."
            linhas += [("", None), ("Células marcadas com X", texto)]
    return linhas


def gerar_xlsx(caminho: Path, tabelas: list[Tabela], contexto: dict) -> None:
    wb = Workbook()
    notas = wb.active
    notas.title = "Notas"
    for i, (a, b) in enumerate(_notas(tabelas, contexto), start=1):
        notas.cell(row=i, column=1, value=a)
        if b is not None:
            notas.cell(row=i, column=2, value=b).alignment = Alignment(wrap_text=True)
    notas["A1"].font = Font(bold=True, size=14)
    if contexto["synthetic"]:
        notas["A2"].font = Font(bold=True, color="C00000")
    notas.column_dimensions["A"].width = 38
    notas.column_dimensions["B"].width = 110
    for t in tabelas:
        ws = wb.create_sheet(t.aba)
        props = {p["name"]: p for p in t.objeto["properties"]}
        df = t.dados
        for j, col in enumerate(df.columns, start=1):
            c = ws.cell(row=1, column=j, value=col)
            c.font, c.fill = ESTILO_CABECALHO, FUNDO_CABECALHO
            ws.column_dimensions[get_column_letter(j)].width = max(12, len(col) + 2)
        for i, linha in enumerate(df.itertuples(index=False), start=2):
            for j, (col, valor) in enumerate(zip(df.columns, linha, strict=True), start=1):
                if pd.isna(valor):
                    valor = "X" if col in t.colunas_suprimidas else None
                elif hasattr(valor, "item"):
                    valor = valor.item()
                celula = ws.cell(row=i, column=j, value=valor)
                fmt = _formato(col, props.get(col))
                if fmt and not isinstance(valor, str):
                    celula.number_format = fmt
        ws.freeze_panes = "A2"
    wb.save(caminho)


# ---------------------------------------------------------------------------
# Sumário (modelo Jinja2 da série)
# ---------------------------------------------------------------------------


def _ambiente(pasta: Path, competencia: str, coluna_periodo: str = "period") -> jinja2.Environment:
    ambiente = jinja2.Environment(
        loader=jinja2.FileSystemLoader(pasta),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=jinja2.select_autoescape(["html"], default=False),  # saída Markdown
    )
    ambiente.filters.update(
        milhar=milhar,
        sinal=sinal,
        pct=pct,
        reais=reais,
        in_period=lambda linhas: [r for r in linhas if str(r.get(coluna_periodo)) == competencia],
    )
    ambiente.globals.update(
        maiores=lambda linhas, col, n: sorted(linhas, key=lambda r: r[col], reverse=True)[:n],
        menores=lambda linhas, col, n: sorted(linhas, key=lambda r: r[col])[:n],
        zip=zip,
    )
    return ambiente


def _registros(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))


def gerar_sumario(unidade: Unidade, modelo: str, tabelas: list[Tabela], contexto: dict) -> str:
    controles = {
        t.porta: {
            **(t.controle or {}),
            "suppressed_cells": t.celulas_suprimidas,
            "removed_rows": t.linhas_removidas,
        }
        for t in tabelas
    }
    ambiente = _ambiente(unidade.fase(7), contexto["edition"]["period"], unidade.projeto.convencoes["period_column"])
    return ambiente.get_template(modelo).render(
        tables={t.porta: _registros(t.dados) for t in tabelas}, controls=controles, **contexto
    )


# ---------------------------------------------------------------------------
# Metadados: Data Package e CKAN
# ---------------------------------------------------------------------------


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def versao_codigo(raiz: Path) -> str:
    try:
        saida = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=raiz, capture_output=True, text=True, check=True
        )
        return saida.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "sem-git"


def _schema_frictionless(t: Tabela) -> dict:
    campos = []
    for prop in t.objeto["properties"]:
        if prop["name"] not in t.dados.columns:
            continue
        campo = {"name": prop["name"], "type": TIPO_FRICTIONLESS[prop["logicalType"]]}
        if prop.get("description"):
            campo["description"] = prop["description"]
        campos.append(campo)
    if "suppressed" in t.dados.columns:
        campos.append(
            {"name": "suppressed", "type": "boolean", "description": "Célula suprimida pelo controle de divulgação."}
        )
    return {"fields": campos, "missingValues": [""]}


def gerar_datapackage(pasta: Path, tabelas: list[Tabela], extras: list[Path], contexto: dict, serie: dict) -> dict:
    produto, ed = contexto["product"], contexto["edition"]
    recursos = []
    for t in tabelas:
        arquivo = pasta / serie["files_per_edition"]["tables"].format(porta=t.porta)
        recursos.append(
            {
                "name": t.porta,
                "title": t.descricao,
                "description": t.contrato["description"].get("purpose", "").strip(),
                "path": arquivo.name,
                "format": "csv",
                "mediatype": "text/csv",
                "encoding": "utf-8",
                "bytes": arquivo.stat().st_size,
                "hash": f"sha256:{_sha256(arquivo)}",
                "schema": _schema_frictionless(t),
                "x-statera": {
                    "contract": t.contrato_id,
                    "urn": contratos.propriedade_custom(t.contrato, "urn"),
                    "contractVersion": t.contrato["version"],
                    "classification": classificacao.resumo(classificacao.da_tabela(t.contrato)),
                    "suppressedCells": t.celulas_suprimidas,
                    **({"removedRows": t.linhas_removidas} if t.linhas_removidas else {}),
                },
            }
        )
    for arquivo in extras:
        formato = arquivo.suffix.lstrip(".")
        recursos.append(
            {
                "name": arquivo.stem.lower().replace("_", "-"),
                "path": arquivo.name,
                "format": formato,
                "mediatype": MEDIATYPES[formato],
                "bytes": arquivo.stat().st_size,
                "hash": f"sha256:{_sha256(arquivo)}",
            }
        )
    sintetico = " (DADOS SINTÉTICOS)" if contexto["synthetic"] else ""
    titulos = dict.fromkeys(f"{e['sourceSystem']} — corte de {e['cutDate']}" for e in contexto.get("extractions", []))
    fontes = [{"title": titulo} for titulo in titulos]
    return {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": f"{produto['id']}-{ed['period']}",
        "title": f"{produto['name']} — {ed['period_long']}{sintetico}",
        "description": " ".join(produto["description"]["purpose"].split()),
        "version": ed["version"],
        "created": ed["generated_at"],
        "keywords": produto.get("tags", []),
        "licenses": [serie["license"]],
        "sources": fontes or [{"title": "Sem extração registrada"}],
        "contributors": [{"title": f"{contexto['curator']} (papel)", "roles": ["publisher"]}],
        "resources": recursos,
        "x-statera": {
            "product": produto["id"],
            "urn": contratos.propriedade_custom(produto, "urn"),
            "period": ed["period"],
            "cutDate": ed["cut_date"],
            "codeVersion": ed["code_version"],
            "run": contexto.get("run"),
            "extractions": contexto.get("extractions", []),
            "cuts": contexto.get("cuts", {}),
            "references": contexto.get("references", []),
            "series": serie["series"],
            "corrects": ed.get("corrects"),
            "synthetic": contexto["synthetic"],
        },
    }


def para_ckan(datapackage: dict, produto: dict, serie: dict) -> dict:
    """Converte o Data Package na carga de `package_create` do CKAN (dados.gov.br)."""
    destino = serie.get("open_data_portal", {"organization": "organizacao-exemplo"})
    x = datapackage["x-statera"]
    return {
        "name": datapackage["name"],
        "title": datapackage["title"],
        "notes": datapackage["description"],
        "license_id": "cc-by",
        "owner_org": destino["organization"],
        "tags": [{"name": t} for t in datapackage.get("keywords", [])],
        "resources": [
            {
                "name": r.get("title", r["name"]),
                "description": r.get("description", ""),
                "format": r["format"].upper(),
                "url": f"<URL após upload>/{r['path']}",
                "hash": r["hash"],
            }
            for r in datapackage["resources"]
        ],
        "extras": [
            {"key": "period", "value": x["period"]},
            {"key": "cut_date", "value": x["cutDate"]},
            {"key": "frequency", "value": contratos.propriedade_custom(produto, "frequency")},
            {"key": "dataset", "value": destino.get("dataset", produto["id"])},
            {"key": "urn", "value": x.get("urn") or ""},
        ],
    }


def curador(unidade: Unidade) -> str:
    responsaveis = unidade.ler("1-specify/owners.yaml") or {}
    return governanca.nome_do_papel(responsaveis.get("curator", ""), unidade.projeto)
