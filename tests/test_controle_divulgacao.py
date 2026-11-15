"""Controle de divulgação de microdados: k-anonimato com supressão local, e a regra do gate."""

import pandas as pd

from statera import classificacao
from statera.analyse.controle_divulgacao import aplicar

REGRA = {
    "method": "k-anonymity",
    "k": 2,
    "quasiIdentifiers": ["municipio", "faixa_etaria"],
    "suppressibleColumns": ["municipio", "faixa_etaria"],
}


def test_combinacao_rara_perde_o_municipio_e_fica_anonima():
    df = pd.DataFrame(
        {
            "municipio": ["A", "A", "B", "C"],
            "faixa_etaria": ["18-24", "18-24", "25-29", "25-29"],
            "saldo": [1, 1, 1, -1],
        }
    )
    r = aplicar(df, REGRA)
    # B/25-29 e C/25-29 eram únicos; sem o município, viram dois registros de 25-29: k = 2
    assert r.celulas_suprimidas == 2 and r.linhas_removidas == 0
    assert r.dados["municipio"].isna().sum() == 2
    assert r.dados.groupby(["faixa_etaria"], dropna=False).size().min() >= 2


def test_o_que_continua_unico_e_removido():
    df = pd.DataFrame({"municipio": ["A", "A", "B"], "faixa_etaria": ["18-24", "18-24", "60+"], "saldo": [1, 1, 1]})
    r = aplicar(df, REGRA)
    assert r.linhas_removidas == 1 and len(r.dados) == 2


def _contrato(controle: dict | None) -> dict:
    classif = {"sharing": "broad", "nature": "deidentified", "secrecy": "statistical"}
    if controle:
        classif["disclosureControl"] = controle
    colunas = [{"name": n, "classification": "broad"} for n in ("municipio", "faixa_etaria", "saldo")]
    return {"customProperties": [{"property": "classification", "value": classif}], "schema": [{"properties": colunas}]}


def test_microdados_desidentificados_exigem_k_anonimato():
    supressao = {"method": "cell-suppression", "column": "saldo", "minimo": 3, "suppressedColumns": ["saldo"]}
    motivos = classificacao.impedimentos_publicacao(_contrato(supressao), ["municipio", "saldo"], True)
    assert any("k-anonymity" in m for m in motivos)
    assert classificacao.impedimentos_publicacao(_contrato(REGRA), ["municipio", "saldo"], True) == []


def test_k_anonimato_com_coluna_inexistente():
    regra = {**REGRA, "quasiIdentifiers": ["municipio", "ocupacao"], "suppressibleColumns": ["municipio"]}
    assert any("'ocupacao' não existe" in e for e in classificacao.coerencia(_contrato(regra)))
