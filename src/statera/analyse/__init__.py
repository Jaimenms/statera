"""Fase 6 — Analisar. Tarefa do processo: `implementacao: py:statera.analyse.step`.

Reúne TODOS os resultados de controle da edição e decide a situação que o gateway usa:
  - testes do dbt (dados, controles SQL CK-*, unit tests das regras), rodados na tarefa anterior;
  - verificação dos contratos ODCS das tabelas construídas (e do bronze de entrada);
  - controles declarados em `6-analyse/controls.yaml` (comparação com a edição anterior).
Os resultados vão para `<produto>/6-analyse/data/<competência>/`.
"""

from __future__ import annotations

import json

from statera import contratos, openlineage, portas
from statera.analyse import controles, verificacao
from statera.disseminate import registro
from statera.edicao import Edicao, agora


def contratos_em_escopo(ed: Edicao) -> set[str]:
    """Contratos verificados na edição: as saídas do próprio produto e os ativos construídos nesta
    execução (staging e referências incluídos). As entradas de produtos internos não são
    reverificadas: o produtor as verificou ao liberar o corte."""
    projeto = ed.projeto
    nos = set(ed.resultados_dbt.get("nos", []))
    escopo = set()
    for cid, (caminho, contrato) in contratos.todos(projeto).items():
        if projeto.unidade_de(caminho) == ed.unidade:
            escopo.add(cid)
            continue
        no = contratos.no_dbt(contrato)
        if no and no[0] in ("model", "seed") and f"{no[0]}.{projeto.dbt_nome}.{no[1]}" in nos:
            escopo.add(cid)
    return escopo


def _gravar(ed: Edicao, nome: str, linhas: list[dict]) -> None:
    pasta = ed.pasta_dados(6)
    pasta.mkdir(parents=True, exist_ok=True)
    conteudo = {**ed.faceta, "registered_at": agora(), "resultados": linhas}
    (pasta / f"{nome}.json").write_text(
        json.dumps(conteudo, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def _linhas(resultados: list[verificacao.Resultado]) -> list[dict]:
    return [
        {
            "contract": r.contrato,
            "verificacao": r.verificacao,
            "ok": bool(r.ok),
            "detalhe": r.detalhe,
            "severity": r.severidade,
        }
        for r in resultados
    ]


def step(ed: Edicao) -> str:
    """GSBPM 6.2 — validar resultados. Devolve 'failed' se algum controle bloqueante falhou."""
    escopo = contratos_em_escopo(ed)
    proprios = [
        c for caminho, c in contratos.todos(ed.projeto).values() if ed.projeto.unidade_de(caminho) == ed.unidade
    ]
    ed.ctx["inputs"] = [openlineage.dataset(contratos.tabela_fisica(c)) for c in proprios]

    verificacoes = verificacao.verificar_todos(escopo, ed.projeto, ed.data_corte)
    _gravar(ed, "contracts", _linhas(verificacoes))
    definicoes = (ed.unidade.ler("6-analyse/controls.yaml") or {}).get("controls", [])
    insumos = controles.Insumos(
        ed.competencia,
        atual=lambda porta: portas.ler_porta(ed.unidade, porta),
        anterior=lambda porta: registro.tabela_anterior(ed.unidade, ed.competencia, porta),
        coluna_periodo=ed.projeto.convencoes["period_column"],
    )
    resultados_ctrl, tabelas = controles.executar(definicoes, insumos)
    _gravar(ed, "controls", _linhas(resultados_ctrl))
    falhas_dbt = ed.resultados_dbt.get("falhas", [])
    _gravar(ed, "dbt-tests", [{"failed": f} for f in falhas_dbt])

    ed.revisoes = tabelas or None
    ed.verificacoes = len(verificacoes)
    ed.falhas_nao_bloqueantes = sum(not r.ok and not r.bloqueia for r in verificacoes)
    ed.log(f"  contratos ODCS: {len(verificacoes)} verificações em {len(escopo)} contratos, no corte {ed.data_corte}")
    for r in resultados_ctrl:
        marca = "ok " if r.ok else ("ERRO" if r.bloqueia else "aviso")
        ed.log(f"  [{marca}] {r.verificacao}: {r.detalhe}")
    bloqueios = [f"dbt: {f}" for f in falhas_dbt]
    bloqueios += [f"{r.contrato} {r.verificacao}: {r.detalhe}" for r in [*verificacoes, *resultados_ctrl] if r.bloqueia]
    if bloqueios:
        ed.falhas += bloqueios
        for b in bloqueios[:10]:
            ed.log(f"  FALHA {b}")
        return "failed"
    return "ok"
