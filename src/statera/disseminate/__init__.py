"""Fase 7 — Disseminar. Tarefas do processo:

    py:statera.disseminate.step         controle de divulgação, gate e geração do pacote (6.4 e 7.2)
    py:statera.disseminate.publish      registro da edição aprovada no registro de edições (7.3)
    py:statera.disseminate.mark_cut  produtos INTERNOS: libera o corte aos consumidores (7.3)

O pacote é gerado antes da revisão e da aprovação humanas; só a edição APROVADA é registrada. Um
produto interno não publica arquivos: libera um corte, com um manifesto imutável.
"""

from __future__ import annotations

import json
import shutil

from statera import contratos, openlineage, portas, referencias
from statera.analyse.controle_divulgacao import (
    ErroClassificacao,
    aplicar_controle_divulgacao,
    verificar_publicacao,
)
from statera.collect import arquivo_corte, extracao
from statera.disseminate import divulgacao, registro
from statera.edicao import Edicao, FalhaNaEdicao, agora
from statera.process import dbt


def _contexto(ed: Edicao, versao: str, retifica: dict | None) -> dict:
    serie = registro.serie(ed.unidade)
    principal = serie["main_resource"]
    controle = next((c for c in (ed.revisoes or {}) if c), None)
    revisoes = (ed.revisoes or {}).get(controle, []) if controle else []
    return {
        "product": portas.odps(ed.unidade),
        "edition": {
            "period": ed.competencia,
            "period_long": divulgacao.competencia_extenso(ed.competencia),
            "cut_date": ed.data_corte,
            "version": versao,
            "corrects": retifica,
            "generated_at": agora(),
            "code_version": divulgacao.versao_codigo(ed.projeto.raiz),
        },
        "revisions": revisoes,
        "main_resource": principal,
        "run": ed.run_id,
        "extractions": ed.extracoes,
        "cuts": {
            nome: {"cutDate": m["cutDate"], "run": m["run"], "urn": m.get("urn")} for nome, m in ed.cortes.items()
        },
        "references": referencias.usadas(ed.unidade, dbt.manifesto(gerar=False, projeto=ed.projeto), ed.cortes),
        "curator": divulgacao.curador(ed.unidade),
        "synthetic": ed.projeto.sintetico,
    }


def step(ed: Edicao) -> str:
    """Gera o pacote da edição em <produto>/7-disseminate/data/<competência>/."""
    unidade = ed.unidade
    if registro.publicada(unidade, ed.competencia) and not ed.forcar:
        raise FalhaNaEdicao(
            f"Edição {ed.competencia} já publicada ({registro.caminho_edicao(unidade, ed.competencia)}). "
            "Publicações não são recalculadas; use --force para retificar."
        )
    tabelas = divulgacao.carregar_tabelas(unidade)
    try:
        for t in tabelas:
            aplicar_controle_divulgacao(t)
        verificar_publicacao(tabelas, unidade.ler("1-specify/classification.yaml") or {})
    except ErroClassificacao as erro:
        raise FalhaNaEdicao(str(erro)) from erro

    pasta = ed.pasta_dados(7)
    if pasta.exists():
        shutil.rmtree(pasta)  # pacote ainda não registrado: pode ser refeito
    pasta.mkdir(parents=True)
    versao, retifica = registro.versao_e_retificacao(unidade, ed.competencia)
    contexto = _contexto(ed, versao, retifica)
    serie = registro.serie(unidade)
    arquivos = serie["files_per_edition"]
    for t in tabelas:
        t.dados.to_csv(pasta / arquivos["tables"].format(porta=t.porta), index=False)
    xlsx = pasta / arquivos["spreadsheet"].format(competencia=ed.competencia)
    divulgacao.gerar_xlsx(xlsx, [t for t in tabelas if t.na_planilha], contexto)  # microdados só em CSV
    sumario = pasta / arquivos["summary"]
    sumario.write_text(divulgacao.gerar_sumario(unidade, "summary.md.j2", tabelas, contexto), encoding="utf-8")

    datapackage = divulgacao.gerar_datapackage(pasta, tabelas, [xlsx, sumario], contexto, serie)
    (pasta / "datapackage.json").write_text(
        json.dumps(datapackage, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    ckan = divulgacao.para_ckan(datapackage, contexto["product"], serie)
    (pasta / "ckan.json").write_text(json.dumps(ckan, indent=2, ensure_ascii=False), encoding="utf-8")

    ed.publicacao = {
        "folder": str(pasta),
        "version": versao,
        "corrects": retifica,
        "suppressed_cells": {t.porta: t.celulas_suprimidas for t in tabelas if t.controle_aplicado},
        "removed_rows": {t.porta: t.linhas_removidas for t in tabelas if t.linhas_removidas},
    }
    ed.ctx["outputs"] = [
        openlineage.dataset(f"{ed.produto}/{ed.competencia}/{r['path']}", "file://publicacao")
        for r in datapackage["resources"]
    ]
    suprimidas = sum(ed.publicacao["suppressed_cells"].values())
    removidas = sum(ed.publicacao["removed_rows"].values())
    extra = f"; {removidas} registros removidos pelo k-anonymity" if removidas else ""
    ed.log(f"  pacote: {ed.projeto.rel(pasta)} (versão {versao}; {suprimidas} células suprimidas{extra})")
    return "ok"


def publish(ed: Edicao) -> str:
    """Registra a edição aprovada (Data Package e avaliação) no registro de edições."""
    unidade = ed.unidade
    pasta = ed.pasta_dados(7)
    datapackage = json.loads((pasta / "datapackage.json").read_text(encoding="utf-8"))
    destino = registro.registrar(unidade, datapackage)
    avaliacoes = registro.pasta_avaliacoes(unidade)
    avaliacoes.mkdir(parents=True, exist_ok=True)
    for arquivo in ed.pasta_dados(8).glob("avaliacao.*"):
        shutil.copy2(arquivo, avaliacoes / f"{ed.competencia}{arquivo.suffix}")
    from statera import documentacao  # import local: documentação depende do registro

    pagina = documentacao.gerar_registro(unidade)
    ed.log(f"  registro: {ed.projeto.rel(destino)} (página da série: {ed.projeto.rel(pagina)})")
    return "ok"


def _contagens_no_corte(ed: Edicao) -> dict:
    """Linhas de cada saída do produto interno na partição do corte."""
    import duckdb

    saida = {}
    con = duckdb.connect(str(ed.projeto.warehouse), read_only=True)
    try:
        for porta in portas.portas_de_saida(ed.unidade):
            contrato = contratos.todos(ed.projeto)[porta["contractId"]][1]
            tabela, particao = (
                contratos.tabela_fisica(contrato),
                contratos.propriedade_custom(contrato, "partitionedBy"),
            )
            if particao:
                linhas = con.execute(
                    f"SELECT count(*) FROM {tabela} WHERE {particao} = CAST(? AS DATE)", [ed.data_corte]
                ).fetchone()[0]
                saida[porta["name"]] = {"contract": contrato["id"], "table": tabela, "rows": linhas}
    finally:
        con.close()
    return saida


def mark_cut(ed: Edicao) -> str:
    """Produto interno: grava o manifesto imutável do corte e o libera aos consumidores autorizados."""
    from datetime import date

    destino = arquivo_corte(ed.unidade, ed.data_corte)
    if destino.exists() and not ed.forcar:
        raise FalhaNaEdicao(f"Corte {ed.data_corte} de {ed.produto} já liberado; cortes são imutáveis (use --force)")
    manifesto = {
        "product": ed.produto,
        "urn": ed.unidade.urn,
        "alignment": ed.unidade.alinhamento,
        "cutDate": ed.data_corte,
        "run": ed.run_id,
        "markedAt": agora(),
        "codeVersion": divulgacao.versao_codigo(ed.projeto.raiz),
        "authorizedConsumers": sorted(portas.consumidores_autorizados(ed.unidade)),
    }
    fontes = extracao.fontes(ed.projeto, ed.unidade)
    if fontes:
        manifesto["sources"] = extracao.manifesto_das_fontes(date.fromisoformat(ed.data_corte), fontes)
    else:
        manifesto["outputs"] = _contagens_no_corte(ed)
        manifesto["references"] = referencias.usadas(
            ed.unidade, dbt.manifesto(gerar=False, projeto=ed.projeto), ed.cortes
        )
    manifesto["inputs"] = ed.cortes
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(manifesto, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    resumo = manifesto.get("sources") or manifesto.get("outputs")
    detalhes = ", ".join(
        f"{nome}: {v['rows']} linhas" + (f" até o lote {v['batch']}" if "batch" in v else "")
        for nome, v in resumo.items()
    )
    ed.log(f"  corte {ed.data_corte} liberado ({detalhes})")
    return "ok"
