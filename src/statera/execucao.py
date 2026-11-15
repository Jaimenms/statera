"""Execução de uma edição: o processo to-be do produto É o plano de execução.

O executor percorre o YAML do processo (`<produto>/2-design/processes/to-be.yaml`), a partir do
evento de início, seguindo os fluxos:

  - tarefa automática      roda a `implementacao` (py:<módulo>.<função> ou dbt:build) e guarda a
                           situação devolvida ("ok" ou "failed"); emite OpenLineage e registra a duração;
  - gateway exclusivo      segue o fluxo cuja `condicao` é a situação da última tarefa;
  - evento de espera       (intermediateCatchEvent com `statera.aguarda`) chama a função; enquanto
                           ela devolver falso, a edição fica "waiting" (ex.: o corte do produto
                           interno de entrada ainda não foi liberado); `statera resume` reavalia;
  - tarefa humana          para e grava o estado ("waiting"); `statera resume` conclui a
                           tarefa (aprovando ou reprovando) e retoma dali. Na demonstração,
                           `--approve` simula as decisões humanas;
  - tarefa de exceção      (statera.excecao: true) a edição fica "blocked", com as falhas;
  - evento de fim          a edição está "done".

Não há sequência de etapas no código: mudar o processo muda a execução, e o BPMN gerado do mesmo
YAML documenta exatamente o que roda (ver especificação, 05-processos-e-execucao.md).
"""

from __future__ import annotations

import importlib
from collections import defaultdict

from statera import openlineage
from statera.design import bpmn, processos
from statera.edicao import Edicao, FalhaNaEdicao
from statera.projeto import Projeto, atual

HUMANAS = ("manual", "semi-automatic")


class ProcessoInvalido(RuntimeError):
    """O processo não pode ser executado como está modelado."""


def _implementacao(referencia: str):
    modulo, _, funcao = referencia.removeprefix("py:").rpartition(".")
    try:
        return getattr(importlib.import_module(modulo), funcao)
    except (ImportError, AttributeError) as erro:
        raise ProcessoInvalido(f"implementação {referencia} não encontrada") from erro


def _datasets(proc: dict, elemento: dict, chave: str) -> list[dict]:
    """Datasets OpenLineage a partir das associações de dados do BPMN (depósitos com `ativo`)."""
    from statera import contratos

    dados = {d["id"]: d for d in proc.get("data", [])}
    por_id = contratos.todos()
    saida = []
    for ref in elemento.get(chave, []):
        ativo = dados.get(ref, {}).get("asset")
        if ativo in por_id:
            saida.append(openlineage.dataset(contratos.tabela_fisica(por_id[ativo][1])))
    return saida


def _executar_tarefa(ed: Edicao, proc: dict, e: dict) -> str:
    ext = e.get("statera", {})
    impl = ext.get("implementation", "")
    entradas, saidas = _datasets(proc, e, "inputs"), _datasets(proc, e, "outputs")
    ed.log(f"{e['id']} — {e['name']}")
    situacao, mensagem = "ok", ""
    with openlineage.execucao(
        f"{ed.produto}.{e['id']}", entradas, saidas, ed.faceta, ed.arquivo_eventos, ed.pai
    ) as ctx:
        ed.ctx = ctx
        try:
            if impl == "dbt:build":
                from statera.process import etapa_dbt

                situacao = etapa_dbt(ed, list(ext.get("models", [])))
            elif impl.startswith("py:"):
                situacao = _implementacao(impl)(ed)
            else:
                raise ProcessoInvalido(f"{e['id']}: implementação '{impl}' em formato desconhecido")
        except FalhaNaEdicao as erro:
            situacao, mensagem = "failed", str(erro)
            ed.falhas.append(f"{e['id']}: {erro}")
    ed.registrar_tarefa(e["id"], situacao, ctx.get("duracao_s"), **({"mensagem": mensagem[:300]} if mensagem else {}))
    ed.salvar()
    return situacao


def _proximo(saidas: dict, e: dict, situacao: str) -> str:
    fluxos = saidas[e["id"]]
    if e["type"] in bpmn.GATEWAYS:
        escolhidos = [f for f in fluxos if f.get("condition") == situacao]
        if len(escolhidos) != 1:
            raise ProcessoInvalido(f"{e['id']}: nenhum fluxo (ou mais de um) com condicao '{situacao}'")
        return escolhidos[0]["to"]
    if len(fluxos) != 1:
        raise ProcessoInvalido(f"{e['id']}: {len(fluxos)} fluxos de saída fora de gateway (paralelismo não suportado)")
    return fluxos[0]["to"]


def _percorrer(ed: Edicao, proc: dict, no: str, situacao: str, aprovar: bool) -> Edicao:
    elementos = {e["id"]: e for e in proc["elements"]}
    saidas: dict[str, list[dict]] = defaultdict(list)
    for f in proc["flows"]:
        saidas[f["from"]].append(f)
    for _ in range(10 * len(elementos)):  # proteção contra laços sem saída
        e = elementos[no]
        ext = e.get("statera", {})
        if e["type"] == "endEvent":
            ed.situacao, ed.pendente = "done", None
            _fim_da_edicao(ed, "COMPLETE")
            ed.log(f"{ed.rotulo}: concluída ({e['name']}).")
            break
        if e["type"] in bpmn.TAREFAS and ext.get("exception"):
            ed.situacao, ed.pendente = "blocked", e["id"]
            _fim_da_edicao(ed, "FAIL")
            ed.log(f"{ed.rotulo} BARRADA: aguardando '{e['name']}' ({_papel(proc, e)}).")
            break
        if ext.get("awaits") and e["type"] not in bpmn.TAREFAS:
            if not _aguardar(ed, e):
                ed.situacao, ed.pendente = "waiting", e["id"]
                ed.log(f"Aguardando '{e['name']}'. Libere a entrada e rode: statera resume")
                break
        elif e["type"] in bpmn.TAREFAS and ext.get("type") in HUMANAS:
            if not aprovar:
                ed.situacao, ed.pendente = "waiting", e["id"]
                ed.log(f"Aguardando '{e['name']}' ({_papel(proc, e)}). Conclua com: statera resume")
                break
            situacao = _concluir_humana(ed, e, "approved", simulada=True)
        elif e["type"] in bpmn.TAREFAS:
            situacao = _executar_tarefa(ed, proc, e)
            if (
                situacao == "failed"
                and saidas[e["id"]]
                and elementos[saidas[e["id"]][0]["to"]]["type"] not in bpmn.GATEWAYS
            ):
                ed.situacao, ed.pendente = "blocked", e["id"]
                _fim_da_edicao(ed, "FAIL")
                ed.log(f"{ed.rotulo} BARRADA em '{e['name']}' (sem caminho de exceção modelado após a tarefa).")
                break
        no = _proximo(saidas, e, situacao)
    else:
        raise ProcessoInvalido("o processo não chegou ao fim: laço sem saída")
    ed.salvar()
    return ed


def _aguardar(ed: Edicao, e: dict) -> bool:
    satisfeito = bool(_implementacao(e["statera"]["awaits"])(ed))
    if satisfeito:
        ed.registrar_tarefa(e["id"], "ok", None, evento=True)
    ed.salvar()
    return satisfeito


def _papel(proc: dict, e: dict) -> str:
    return next((r["role"] for r in proc["lanes"] if r["id"] == e["lane"]), "?")


def _concluir_humana(ed: Edicao, e: dict, decisao: str, simulada: bool = False) -> str:
    """Conclui uma tarefa humana. Se ela tiver implementação (semiautomática), roda-a."""
    ext = e.get("statera", {})
    ed.log(f"{e['id']} — {e['name']}: {decisao}{' (simulado)' if simulada else ''}")
    if ext.get("implementation") and decisao == "approved":
        ed.ctx = {}
        _implementacao(ext["implementation"])(ed)
    ed.registrar_tarefa(e["id"], decisao, None, humana=True, simulada=simulada)
    return decisao


def _fim_da_edicao(ed: Edicao, tipo: str) -> None:
    openlineage.emitir(
        openlineage.evento(tipo, f"{ed.produto}.edicao", ed.run_id, [], [], ed.faceta), ed.arquivo_eventos
    )


def calendario(produto, competencia: str) -> dict:
    edicoes = {e["period"]: e for e in produto.ler("1-specify/calendar.yaml")["editions"]}
    if competencia not in edicoes:
        raise FalhaNaEdicao(f"Competência {competencia} fora do calendar.yaml; informe --cut-date")
    return edicoes[competencia]


def executar(
    produto: str,
    competencia: str | None = None,
    data_corte: str | None = None,
    forcar: bool = False,
    aprovar: bool = False,
    projeto: Projeto | None = None,
    com_dependencias: bool = False,
) -> Edicao:
    """Executa uma edição do início, pelo processo to-be do produto.

    Produto interno (fonte ou agregado): a "edição" é um corte; informe só a data de corte.
    Com `com_dependencias`, libera antes os cortes que faltam nos produtos internos de entrada.
    """
    projeto = projeto or atual()
    unidade = projeto.produto(produto)
    if unidade.interno:
        if not data_corte:
            raise FalhaNaEdicao(f"{produto} é um produto interno: informe --cut-date")
        competencia = competencia or data_corte
    elif not competencia:
        raise FalhaNaEdicao(f"{produto}: informe --period")
    data_corte = data_corte or calendario(unidade, competencia)["cut_date"]
    if com_dependencias:
        garantir_dependencias(unidade, data_corte, aprovar)
    proc = processos.carregar(unidade.processo_to_be)
    if erros := processos.validar_estrutura(proc):
        raise ProcessoInvalido("; ".join(erros))
    ed = Edicao(produto, competencia, data_corte, forcar)
    ed.run_id = openlineage.novo_run_id()
    openlineage.emitir(
        openlineage.evento("START", f"{produto}.edicao", ed.run_id, [], [], ed.faceta), ed.arquivo_eventos
    )
    corte = "" if unidade.interno else f" — data de corte {data_corte}"
    ed.log(f"{ed.rotulo}{corte} — processo {projeto.rel(unidade.processo_to_be)}")
    inicio = next(e["id"] for e in proc["elements"] if e["type"] == "startEvent")
    return _percorrer(ed, proc, inicio, "ok", aprovar)


def continuar(produto: str, competencia: str, decisao: str = "approved", aprovar: bool = False) -> Edicao:
    """Conclui a tarefa humana pendente (aprovado | reprovado), ou reavalia o evento de espera, e retoma."""
    unidade = atual().produto(produto)
    ed = Edicao.carregar(unidade, competencia)
    if ed.situacao != "waiting" or not ed.pendente:
        raise FalhaNaEdicao(f"{ed.rotulo} não está aguardando tarefa humana nem evento (situação: {ed.situacao})")
    proc = processos.carregar(unidade.processo_to_be)
    elemento = next(e for e in proc["elements"] if e["id"] == ed.pendente)
    ed.situacao = "running"
    if (elemento.get("statera") or {}).get("awaits") and elemento["type"] not in bpmn.TAREFAS:
        if not _aguardar(ed, elemento):
            ed.situacao = "waiting"
            ed.log(f"Ainda aguardando '{elemento['name']}'.")
            ed.salvar()
            return ed
        situacao = "ok"
    else:
        situacao = _concluir_humana(ed, elemento, decisao)
    saidas: dict[str, list[dict]] = defaultdict(list)
    for f in proc["flows"]:
        saidas[f["from"]].append(f)
    return _percorrer(ed, proc, _proximo(saidas, elemento, situacao), situacao, aprovar)


def estado(produto: str, competencia: str) -> Edicao:
    return Edicao.carregar(atual().produto(produto), competencia)


def garantir_dependencias(unidade, data_corte: str, aprovar: bool = False) -> None:
    """Libera, em ordem, os cortes que faltam nos produtos internos de que a unidade depende.

    Em produção, quem faz isso é o orquestrador (dependências entre ativos). O corte já liberado é
    reaproveitado: dois consumidores no mesmo corte não refazem a extração nem a camada canônica.
    """
    from statera import portas
    from statera.collect import ler_corte

    for entrada in portas.produtos_de_entrada(unidade):
        garantir_dependencias(entrada, data_corte, aprovar)
        if ler_corte(entrada, data_corte) is not None:
            print(f"[statera] corte {data_corte} de {entrada.nome} já liberado: reaproveitado", flush=True)
            continue
        ed = executar(entrada.nome, data_corte=data_corte, aprovar=aprovar, projeto=unidade.projeto)
        if ed.situacao != "done":
            detalhe = "\n  - ".join(ed.falhas[:10])
            raise FalhaNaEdicao(
                f"Dependência {entrada.nome} {data_corte} {ed.situacao} em {ed.pendente}:\n  - {detalhe}"
            )
