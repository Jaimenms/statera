"""YAML espelho do BPMN: condições dos fluxos vão e voltam pelo BPMN padrão."""

import yaml
from conftest import PROCESSO

from statera.design import bpmn, processos


def test_condicoes_viram_conditionexpression_e_voltam(tmp_path):
    proc = yaml.safe_load(PROCESSO)
    xml = processos.gerar_xml(proc, "to-be.yaml")
    assert "${situacao == 'failed'}" in xml and 'xmlns:statera="urn:statera:bpmn:1.0"' in xml
    arquivo = tmp_path / "to-be.bpmn"
    arquivo.write_text(xml, encoding="utf-8")
    de_volta = processos.para_dict(arquivo)
    assert de_volta["flows"] == proc["flows"]
    assert de_volta["elements"] == proc["elements"]


def test_resumo_de_esforco_ignora_tarefas_de_excecao(tmp_path):
    arquivo = tmp_path / "to-be.bpmn"
    arquivo.write_text(processos.gerar_xml(yaml.safe_load(PROCESSO), "to-be.yaml"), encoding="utf-8")
    r = bpmn.resumo(bpmn.ler(arquivo))
    assert r["manual_h"] == 0.5  # Aprovar (30 min); Investigar é exceção
