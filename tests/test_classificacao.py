"""Classificação em quatro dimensões e o gate de publicação aberta."""

from statera import classificacao


def _contrato(tabela: dict, colunas: list[dict]) -> dict:
    return {"customProperties": [{"property": "classification", "value": tabela}], "schema": [{"properties": colunas}]}


AMPLO = {"sharing": "broad", "nature": "aggregated", "secrecy": "none"}


def test_tabela_ampla_e_agregada_pode_ser_publicada():
    c = _contrato(AMPLO, [{"name": "saldo", "classification": "broad"}])
    assert classificacao.coerencia(c) == []
    assert classificacao.impedimentos_publicacao(c, ["saldo"], controle_aplicado=False) == []


def test_coluna_pessoal_exige_natureza_pessoal():
    c = _contrato(AMPLO, [{"name": "cpf", "classification": "broad", "tags": ["dado-pessoal"]}])
    assert any("nature" in e for e in classificacao.coerencia(c))
    assert any("dado pessoal" in m for m in classificacao.impedimentos_publicacao(c, ["cpf"], False))


def test_sigilo_estatistico_exige_controle_aplicado():
    controle = {
        "method": "cell-suppression",
        "column": "n",
        "minimo": 3,
        "suppressedColumns": ["x"],
        "unpublishedColumns": ["n"],
    }
    c = _contrato(
        {**AMPLO, "secrecy": "statistical", "disclosureControl": controle},
        [{"name": "x", "classification": "broad"}, {"name": "n", "classification": "restricted"}],
    )
    assert classificacao.coerencia(c) == []  # coluna restrita não publicada é permitida
    assert classificacao.impedimentos_publicacao(c, ["x"], controle_aplicado=False)
    assert classificacao.impedimentos_publicacao(c, ["x"], controle_aplicado=True) == []


def test_restrito_nunca_vai_para_publicacao_aberta():
    c = _contrato({"sharing": "restricted", "nature": "deidentified", "secrecy": "none"}, [])
    assert any("sharing" in m for m in classificacao.impedimentos_publicacao(c, [], True))


def test_aprovacao_precisa_coincidir_com_o_contrato():
    c = _contrato(AMPLO, [])
    assert classificacao.aprovacao_confere(c, {**AMPLO}) == []
    assert classificacao.aprovacao_confere(c, {**AMPLO, "nature": "personal"})
    assert classificacao.aprovacao_confere(c, None) == ["sem aprovação em classification.yaml"]
