"""Esqueleto do produto: os mesmos arquivos em todo produto, e o calendário gerado dos internos."""

import yaml

from statera import calendario, validacao
from statera.projeto import atual


def test_calendario_do_interno_vem_dos_consumidores(projeto_minimo):
    p = atual()
    pedidos = calendario.pedidos(p, p.produto("dados-base"))
    assert pedidos == {"2026-02-20": ["consumidor 2026-01", "consumidor 2026-02"]}  # um corte, duas edições
    texto = calendario.gerar(p, p.produto("dados-base"))
    assert texto.startswith("# ARQUIVO GERADO") and yaml.safe_load(texto)["cuts"][0]["cut_date"] == "2026-02-20"


def test_nao_se_aplica_conta_como_presente_mas_sem_conteudo(projeto_minimo):
    p = atual()
    u = p.produto("dados-base")
    arquivo = u.pasta / "7-disseminate" / "series.yaml"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text('not_applicable: "produto interno: não é divulgado"\n', encoding="utf-8")
    assert u.ler("7-disseminate/series.yaml") is None and not u.tem("7-disseminate/series.yaml")
    assert u.nao_se_aplica("7-disseminate/series.yaml") == "produto interno: não é divulgado"


def test_esqueleto_incompleto_e_apontado(projeto_minimo):
    mensagens = [a.mensagem for a in validacao.verificar_esqueleto(atual()) if not a.ok]
    assert any("dados-base: falta 6-analyse/controls.yaml" in m for m in mensagens)
