"""Propriedades do controle de revisão entre edições (Hypothesis)."""

import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from statera.analyse.controles import Insumos, executar, revisoes

valores = st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=12)


@given(valores)
def test_edicoes_iguais_nao_tem_revisao(admissoes):
    df = pd.DataFrame({"competencia": [f"2026-{i + 1:02d}" for i in range(len(admissoes))], "admissoes": admissoes})
    r = revisoes(df, df.copy(), ["competencia"], ["admissoes"], "admissoes")
    assert (r["revisao_admissoes"] == 0).all() and (r["revisao_relativa"] == 0).all()


@given(st.integers(min_value=1, max_value=10_000), st.integers(min_value=0, max_value=10_000))
def test_revisao_relativa_e_sobre_a_edicao_anterior(antes, depois):
    atual = pd.DataFrame({"competencia": ["2026-01"], "admissoes": [depois]})
    anterior = pd.DataFrame({"competencia": ["2026-01"], "admissoes": [antes]})
    r = revisoes(atual, anterior, ["competencia"], ["admissoes"], "admissoes")
    assert abs(r["revisao_relativa"].iloc[0] - abs(depois - antes) / antes) < 1e-9


def test_sem_edicao_anterior_o_controle_passa():
    atual = pd.DataFrame({"competencia": ["2026-01"], "admissoes": [5]})
    definicao = {
        "id": "CK-04",
        "name": "Revisão",
        "type": "revision-between-editions",
        "port": "p",
        "key": ["competencia"],
        "measures": ["admissoes"],
        "relative_base": "admissoes",
        "warning_threshold": 0.03,
        "error_threshold": 0.1,
    }
    resultados, tabelas = executar([definicao], Insumos("2026-01", lambda p: atual, lambda p: None))
    assert resultados[0].ok and tabelas == {}
