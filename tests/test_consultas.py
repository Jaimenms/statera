"""Consultas catalogadas: parâmetros tipados, controle de divulgação e testes com dados sintéticos."""

import textwrap

import pytest

from statera.consume import queries


@pytest.fixture
def consulta(tmp_path):
    pasta = tmp_path / "contagem-por-uf"
    pasta.mkdir()
    (pasta / "query.yaml").write_text(
        textwrap.dedent("""
        id: br.gov.exemplo.query.contagem-por-uf
        title: Contagem por UF
        description: teste
        theme: teste
        bases: [silver.eventos]
        frequency: on-demand
        purpose: study
        parameters:
          - {name: uf, type: uf, required: true}
        limitations: nenhuma
        output_classification:
          sharing: broad
          nature: aggregated
          secrecy: statistical
          disclosureControl: {method: cell-suppression, column: n, minimum: 3, suppressedColumns: [n]}
        owner: equipe-x
        status: cataloged
        version: 0.1.0
    """),
        encoding="utf-8",
    )
    (pasta / "contagem_por_uf.sql").write_text(
        "select municipio, count(*) as n from {{ ref('eventos') }} where uf = '{{ var(\"uf\") }}' group by 1 order by 1",
        encoding="utf-8",
    )
    (pasta / "test.yaml").write_text(
        textwrap.dedent("""
        cases:
          - id: suprime_municipio_pequeno
            description: Município com menos de 3 eventos é suprimido.
            parameters: {uf: SP}
            inputs:
              eventos:
                - {uf: SP, municipio: A}
                - {uf: SP, municipio: A}
                - {uf: SP, municipio: A}
                - {uf: SP, municipio: B}
                - {uf: RJ, municipio: C}
            expected:
              - {municipio: A, n: 3, suppressed: false}
              - {municipio: B, n: null, suppressed: true}
    """),
        encoding="utf-8",
    )
    return queries.Query(pasta, queries.ler_yaml(pasta / "query.yaml"))


def test_parametros_validados_pelo_tipo(consulta):
    assert queries.validate_parameters(consulta, {"uf": "SP"}) == {"uf": "SP"}
    with pytest.raises(queries.QueryError):
        queries.validate_parameters(consulta, {"uf": "SP' --"})
    with pytest.raises(queries.QueryError, match="obrigatório"):
        queries.validate_parameters(consulta, {})
    with pytest.raises(queries.QueryError, match="não declarados"):
        queries.validate_parameters(consulta, {"uf": "SP", "extra": "1"})


def test_casos_de_teste_rodam_em_memoria(consulta):
    assert queries.test(consulta) == []


def test_teste_falha_quando_o_resultado_muda(consulta):
    sql = consulta.sql.read_text().replace("count(*)", "count(*) + 1")
    consulta.sql.write_text(sql)
    assert queries.test(consulta)
