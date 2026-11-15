"""Casos de regras em modelos Python: executados pela plataforma, chamando transform()."""

import textwrap

from statera.process import regras
from statera.projeto import atual

MODELO = textwrap.dedent(
    """
    def transform(inputs, variaveis):
        df = inputs["int_teste__base"].copy()
        df["dobro"] = df["value"] * 2
        return df

    def model(dbt, session):
        return transform({"int_teste__base": dbt.ref("int_teste__base").df()}, {})
    """
)


def _regra(raiz, esperado, requer=""):
    pasta = raiz / "products/teste/5-process/rules/RN-T-001-dobro"
    pasta.mkdir(parents=True)
    (pasta / "rule.md").write_text("# RN-T-001 — Dobro\n", encoding="utf-8")
    (pasta / "cases.yaml").write_text(
        f"rule: RN-T-001\nmodel: teste_dobro\n{requer}cases:\n"
        f"  - id: dobra\n    description: dobra o valor\n"
        f"    inputs: {{int_teste__base: [{{value: 2}}, {{value: 5}}]}}\n"
        f"    expected: {esperado}\n",
        encoding="utf-8",
    )
    modelos = raiz / "products/teste/5-process/models/marts/teste"
    modelos.mkdir(parents=True)
    (modelos / "teste_dobro.py").write_text(MODELO, encoding="utf-8")


def test_caso_python_passa_e_nao_vira_unit_test_do_dbt(projeto_minimo):
    _regra(projeto_minimo.raiz, "[{value: 5, dobro: 10}, {value: 2, dobro: 4}]")
    resultados = regras.testar_casos_python(atual())
    assert [(r.regra, r.situacao) for r in resultados] == [("RN-T-001", "ok")]
    assert "teste_dobro" not in regras.gerar_unit_tests(atual())


def test_caso_python_que_falha_mostra_a_diferenca(projeto_minimo):
    _regra(projeto_minimo.raiz, "[{value: 2, dobro: 5}, {value: 5, dobro: 10}]")
    (r,) = regras.testar_casos_python(atual())
    assert r.situacao == "failed" and "esperado" in r.mensagem


def test_caso_que_requer_pspp_ausente(projeto_minimo, monkeypatch):
    monkeypatch.setenv("STATERA_PSPP", "comando-que-nao-existe")
    _regra(projeto_minimo.raiz, "[{value: 2, dobro: 4}, {value: 5, dobro: 10}]", requer="requires: [pspp]\n")
    assert regras.testar_casos_python(atual())[0].situacao == "not-run"
    monkeypatch.setenv("STATERA_EXIGIR", "pspp")  # no CI: ferramenta exigida
    assert regras.testar_casos_python(atual())[0].situacao == "failed"
