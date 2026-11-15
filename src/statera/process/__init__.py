"""Fase 5 — Processar. Tarefa do processo: `implementacao: dbt:build` (com a lista de `modelos`).

`modelos` são seletores do dbt usados como estão:
  - `+modelo` constrói o modelo e tudo o que vem antes (o produto agregado constrói os staging das
    fontes e as referências);
  - `modelo` constrói só o modelo: um produto consumidor NÃO reconstrói o produto interno de que
    depende, lê o corte já liberado. O executor acrescenta só as referências (seeds) necessárias.
As variáveis da edição vão ao dbt: data de corte, competência e os lotes do corte de cada fonte.
Falhas de teste NÃO barram aqui: são resultados de controle, avaliados na tarefa seguinte (GSBPM
6.2), que decide o caminho no gateway.
"""

from __future__ import annotations

from statera import contratos, openlineage
from statera.edicao import Edicao
from statera.process import dbt, regras


def _eventos_por_modelo(ed: Edicao, manifesto: dict, resultados: list[dict]) -> None:
    """Eventos OpenLineage (START/COMPLETE) por modelo dbt executado, a partir do manifest."""
    nos = {**manifesto["nodes"], **manifesto["sources"]}

    def nome(uid: str) -> str:
        no = nos[uid]
        return f"{no['schema']}.{no.get('alias') or no.get('identifier') or no['name']}"

    for r in resultados:
        uid = r["unique_id"]
        if not uid.startswith("model.") or r["status"] != "success":
            continue
        no = manifesto["nodes"][uid]
        entradas = [openlineage.dataset(nome(d)) for d in no["depends_on"]["nodes"] if d in nos]
        run_id = openlineage.novo_run_id()
        for tipo in ("START", "COMPLETE"):
            ev = openlineage.evento(
                tipo, f"dbt.{no['name']}", run_id, entradas, [openlineage.dataset(nome(uid))], ed.faceta, ed.pai
            )
            openlineage.emitir(ev, ed.arquivo_eventos)


def etapa_dbt(ed: Edicao, modelos: list[str]) -> str:
    projeto = ed.projeto
    pendentes = dbt.desatualizados(projeto)
    if pendentes or not regras.esta_sincronizado(projeto):
        ed.falhas.append("Arquivos gerados do dbt desatualizados: rode `statera dbt generate` e `statera rules sync`")
        return "failed"
    dbt.garantir_pacotes(projeto)
    from statera.collect import lotes_dos_cortes

    seletor = []
    if modelos:
        sementes = [f"+{m},resource_type:seed" for m in modelos if not m.startswith("+")]
        seletor = ["--select", *modelos, *sementes]
    variaveis = {"cut_date": ed.data_corte, "period": ed.competencia, "batches": lotes_dos_cortes(ed.cortes)}
    processo = dbt.executar(["build", *seletor], variaveis)
    resultados = dbt.resultados(projeto)
    if processo.returncode != 0 and not resultados:
        ed.falhas.append(f"dbt build não executou:\n{processo.stdout[-2000:]}")
        return "failed"
    total, com_falha = dbt.contagem_testes(resultados)
    ed.resultados_dbt = {
        "tests": total,
        "testes_com_falha": com_falha,
        "falhas": dbt.falhas(resultados),
        "nos": [r["unique_id"] for r in resultados],
    }
    manifesto = dbt.manifesto(gerar=False, projeto=projeto)
    _eventos_por_modelo(ed, manifesto, resultados)
    saidas = [c for caminho, c in contratos.todos(projeto).values() if projeto.unidade_de(caminho) == ed.unidade]
    ed.ctx["outputs"] = [openlineage.dataset(contratos.tabela_fisica(c)) for c in saidas]
    resumo = processo.stdout.strip().splitlines()[-1].split("Done. ")[-1] if processo.stdout.strip() else ""
    ed.log(f"  dbt build: {resumo}")
    return "ok"
