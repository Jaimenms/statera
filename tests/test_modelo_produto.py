"""O modelo Copier gera um produto que nasce no nível 1 (catalogado), exceto pelo que só a equipe sabe."""

from copier import run_copy

from statera import conformidade, validacao
from statera.projeto import ESQUELETO, PACOTE, atual, ler_yaml


def test_novo_produto_so_pede_onde_se_acessa(projeto_minimo):
    destino = projeto_minimo.raiz / "products" / "rais-mensalizada"
    dados = {
        "product": "rais-mensalizada",
        "title": "RAIS mensalizada",
        "namespace": "br.gov.exemplo",
        "curator": "equipe-x",
        "technical_owner": "equipe-x",
        "encarregado": "equipe-x",
    }
    run_copy(str(PACOTE / "templates" / "product"), str(destino), data=dados, defaults=True, quiet=True)
    assert (destino / ".copier-answers.yml").exists()  # permite `copier update` quando o modelo evoluir
    odps = ler_yaml(destino / "1-specify" / "product.odps.yaml")
    assert list(validacao._validador("odps").iter_errors(odps)) == []
    for arquivo, esquema in (("classification.yaml", "classification"), ("owners.yaml", "owners")):
        assert list(validacao._validador(esquema).iter_errors(ler_yaml(destino / "1-specify" / arquivo))) == []
    unidade = atual().produto("rais-mensalizada")
    pendencias = conformidade.REQUISITOS[1](unidade, {"contracts": {}})
    assert len(pendencias) == 1 and "distributions" in pendencias[0]  # nível 1 exige onde o público acessa
    # esqueleto completo: o que ainda não existe declara por quê, em vez de faltar
    for rel in ESQUELETO:
        assert (destino / rel).exists(), rel
        assert unidade.nao_se_aplica(rel).startswith("a de"), rel


def test_produto_interno_pede_consumidores_autorizados(projeto_minimo):
    destino = projeto_minimo.raiz / "products" / "vinculos"
    dados = {"product": "vinculos", "title": "Vínculos", "namespace": "br.gov.exemplo", "alignment": "aggregate",
             "curator": "equipe-x", "technical_owner": "equipe-x", "encarregado": "equipe-x"}  # fmt: skip
    run_copy(str(PACOTE / "templates" / "product"), str(destino), data=dados, defaults=True, quiet=True)
    odps = ler_yaml(destino / "1-specify" / "product.odps.yaml")
    assert list(validacao._validador("odps").iter_errors(odps)) == []
    unidade = atual().produto("vinculos")
    assert (unidade.alinhamento, unidade.interno) == ("aggregate", True)
    pendencias = conformidade.REQUISITOS_INTERNO[1](unidade, {"contracts": {}})
    assert any("authorizedConsumers" in p for p in pendencias)  # nível 1 exige quem pode ler
    assert "não é divulgado" in unidade.nao_se_aplica("7-disseminate/series.yaml")
    assert not (destino / "1-specify/calendar.yaml").exists()  # gerado dos consumidores (sincronizar)
