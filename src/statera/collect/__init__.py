"""Fase 4 — Coletar, e a ligação entre produtos por CORTE.

Implementações usadas nos processos:
    py:statera.collect.step         extração incremental de um produto de fonte (tarefa)
    py:statera.collect.pin_cut   espera e fixa o corte dos produtos internos de entrada
                                       (evento intermediário de mensagem, `statera.aguarda`)

O manifesto de corte de um produto interno fica em `<produto>/7-disseminate/data/<data>/cut.json`
e é escrito por `statera.disseminate.mark_cut`.
"""

from __future__ import annotations

import json
from datetime import date

from statera import openlineage, portas
from statera.collect import extracao
from statera.edicao import Edicao
from statera.projeto import Unidade


def arquivo_corte(unidade: Unidade, data_corte: str):
    return unidade.dados(7) / data_corte / "cut.json"


def ler_corte(unidade: Unidade, data_corte: str) -> dict | None:
    caminho = arquivo_corte(unidade, data_corte)
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else None


def cortes_liberados(unidade: Unidade) -> list[str]:
    return sorted(p.parent.name for p in unidade.dados(7).glob("*/cut.json"))


def _fontes(manifestos: dict) -> list[dict]:
    """Extrações (fonte, lote, linhas, hash) de manifestos aninhados: fonte direta ou via agregado."""
    saida = []
    for m in manifestos.values():
        for tabela, f in (m.get("sources") or {}).items():
            saida.append({"source": tabela, **f})
        saida += _fontes(m.get("inputs") or {})
    return saida


def extracoes_dos_cortes(manifestos: dict) -> list[dict]:
    vistas = {}
    for f in _fontes(manifestos):
        vistas[(f["source"], f["cutDate"])] = {
            "source": f["source"], "sourceSystem": f["sourceSystem"], "cutDate": f["cutDate"],
            "batch": f["batch"], "rows": f["rows"], "sha256": f["sha256"],
        }  # fmt: skip
    return list(vistas.values())


def lotes_dos_cortes(manifestos: dict) -> dict[str, int]:
    """{tabela do bronze: lote máximo} para o filtro `statera.at_cut` dos modelos staging."""
    return {f["source"]: f["batch"] for f in _fontes(manifestos)}


def step(ed: Edicao) -> str:
    """GSBPM 4.3 — extração incremental das fontes do produto de fonte para o bronze."""
    fontes = extracao.fontes(ed.projeto, ed.unidade)
    if not fontes:
        ed.falhas.append(f"{ed.produto}: nenhum contrato do bronze com customProperties.extraction")
        return "failed"
    ed.ctx["inputs"] = [openlineage.dataset(f.origem, f"sistema://{f.sistema}") for f in fontes]
    ed.ctx["outputs"] = [openlineage.dataset(f"bronze.{f.tabela}") for f in fontes]
    for m in extracao.extrair_incremento(date.fromisoformat(ed.data_corte), fontes):
        if m["batch"] is None:
            ed.log(f"  bronze {m['source']}: corte dentro da marca d'água ({m['marca_dagua']}); nenhum lote novo")
        else:
            ed.log(
                f"  bronze {m['source']}: lote {m['batch']} com {m['rows']} linhas "
                f"({m['window_start'] or 'início'} a {m['window_end']})"
            )
    extracao.publicar_bronze_no_warehouse(fontes, ed.projeto)
    ed.cortes = {}
    return "ok"


def pin_cut(ed: Edicao) -> bool:
    """Espera os cortes dos produtos internos de entrada; quando todos existem, fixa-os na edição."""
    entradas = portas.produtos_de_entrada(ed.unidade)
    faltando, manifestos = [], {}
    for unidade in entradas:
        manifesto = ler_corte(unidade, ed.data_corte)
        if manifesto is None:
            faltando.append(unidade.nome)
        else:
            manifestos[unidade.nome] = manifesto
    if faltando:
        ed.log(f"  corte {ed.data_corte} ainda não liberado por: {', '.join(faltando)}")
        return False
    ed.cortes = manifestos
    ed.extracoes = extracoes_dos_cortes(manifestos)
    if fontes := extracao.fontes(ed.projeto):
        alvo = {f.tabela for f in fontes} & set(lotes_dos_cortes(manifestos))
        extracao.publicar_bronze_no_warehouse([f for f in fontes if f.tabela in alvo], ed.projeto)
    fixados = ", ".join(f"{nome} (execução {m['run'][:8]})" for nome, m in manifestos.items())
    ed.log(f"  corte {ed.data_corte} fixado: {fixados or 'nenhum produto interno de entrada'}")
    return True
