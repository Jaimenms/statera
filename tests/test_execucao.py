"""O processo to-be é o plano de execução: semântica do executor."""

import etapas_de_teste
import pytest

from statera import execucao
from statera.edicao import ler_jsonl


@pytest.fixture(autouse=True)
def limpar_chamadas():
    etapas_de_teste.CHAMADAS.clear()


def test_para_na_tarefa_humana_e_retoma(projeto_minimo):
    ed = execucao.executar("teste", "2026-01")
    assert (ed.situacao, ed.pendente) == ("waiting", "T_H")
    assert etapas_de_teste.CHAMADAS == [("A", "2026-01")]
    ed = execucao.continuar("teste", "2026-01")  # aprova -> para na tarefa semiautomática
    assert (ed.situacao, ed.pendente) == ("waiting", "T_P")
    ed = execucao.continuar("teste", "2026-01")  # conclui a publicação (roda a implementação)
    assert ed.situacao == "done"
    assert etapas_de_teste.CHAMADAS[-1] == ("P", "2026-01")
    assert [h["tarefa"] for h in ed.historico] == ["T_A", "T_B", "T_H", "T_P"]


def test_reprovacao_volta_pelo_fluxo_modelado(projeto_minimo):
    execucao.executar("teste", "2026-01")
    ed = execucao.continuar("teste", "2026-01", decisao="rejected")
    assert (ed.situacao, ed.pendente) == ("waiting", "T_H")


def test_falha_segue_o_caminho_de_excecao(projeto_minimo, monkeypatch):
    monkeypatch.setenv("ETAPA_DECISAO", "failed")
    ed = execucao.executar("teste", "2026-01", aprovar=True)
    assert (ed.situacao, ed.pendente) == ("blocked", "T_X")


def test_aprovar_simula_as_tarefas_humanas(projeto_minimo):
    ed = execucao.executar("teste", "2026-01", aprovar=True)
    assert ed.situacao == "done"
    assert any(h.get("simulada") for h in ed.historico)


def test_registra_duracao_e_eventos_openlineage_validos(projeto_minimo):
    from statera import openlineage

    ed = execucao.executar("teste", "2026-01", aprovar=True)
    pasta = projeto_minimo.produto("teste").pasta_execucoes
    linhas = ler_jsonl(pasta / "execucoes.jsonl")
    assert {linha["tarefa_bpmn"] for linha in linhas} >= {"T_A", "T_B"}
    eventos = ler_jsonl(pasta / "eventos.jsonl")
    assert all(openlineage.validar(ev) == [] for ev in eventos)
    assert {ev["eventType"] for ev in eventos if ev["job"]["name"] == "teste.edicao"} == {"START", "COMPLETE"}
    assert eventos[0]["job"]["namespace"] == "br.gov.exemplo"
    assert ed.run_id


def test_consumidor_espera_o_corte_e_retoma_quando_liberado(projeto_minimo):
    ed = execucao.executar("consumidor", "2026-01")
    assert (ed.situacao, ed.pendente) == ("waiting", "Ev_Corte")
    assert etapas_de_teste.CHAMADAS == []
    ed = execucao.continuar("consumidor", "2026-01")  # corte ainda não liberado: continua esperando
    assert (ed.situacao, ed.pendente) == ("waiting", "Ev_Corte")
    corte = execucao.executar("dados-base", data_corte="2026-02-20")
    assert corte.situacao == "done"
    ed = execucao.continuar("consumidor", "2026-01")
    assert ed.situacao == "done"
    assert ed.cortes["dados-base"]["run"] == corte.run_id  # a edição fixa o corte que leu
    assert [h["tarefa"] for h in ed.historico] == ["Ev_Corte", "T_Calcular"]


def test_produto_interno_exige_data_de_corte(projeto_minimo):
    with pytest.raises(execucao.FalhaNaEdicao, match="cut-date"):
        execucao.executar("dados-base")


def test_com_dependencias_libera_o_corte_e_o_reaproveita(projeto_minimo):
    ed = execucao.executar("consumidor", "2026-01", com_dependencias=True)
    assert ed.situacao == "done"
    ed = execucao.executar("consumidor", "2026-02", com_dependencias=True)  # mesma data de corte
    assert ed.situacao == "done"
    extracoes = [c for c in etapas_de_teste.CHAMADAS if c[0] == "E"]
    assert extracoes == [("E", "2026-02-20")]  # um só corte para as duas edições
