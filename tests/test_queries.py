"""Cataloged queries: typed parameters, statistical disclosure control and tests with synthetic data."""

import textwrap

import pytest

from statera.consume import queries


@pytest.fixture
def query(tmp_path):
    folder = tmp_path / "count-by-uf"
    folder.mkdir()
    (folder / "query.yaml").write_text(
        textwrap.dedent("""
        id: org.example.query.count-by-uf
        title: Count by UF
        description: test
        theme: test
        bases: [silver.events]
        frequency: on-demand
        purpose: study
        parameters:
          - {name: uf, type: uf, required: true}
        limitations: none
        output_classification:
          sharing: broad
          nature: aggregated
          secrecy: statistical
          disclosureControl: {method: cell-suppression, column: n, minimum: 3, suppressedColumns: [n]}
        owner: team-x
        status: cataloged
        version: 0.1.0
    """),
        encoding="utf-8",
    )
    (folder / "count_by_uf.sql").write_text(
        "select municipality, count(*) as n from {{ ref('events') }} where uf = '{{ var(\"uf\") }}' group by 1 order by 1",
        encoding="utf-8",
    )
    (folder / "test.yaml").write_text(
        textwrap.dedent("""
        cases:
          - id: suppresses_small_municipality
            description: A municipality with fewer than 3 events is suppressed.
            parameters: {uf: SP}
            inputs:
              events:
                - {uf: SP, municipality: A}
                - {uf: SP, municipality: A}
                - {uf: SP, municipality: A}
                - {uf: SP, municipality: B}
                - {uf: RJ, municipality: C}
            expected:
              - {municipality: A, n: 3, suppressed: false}
              - {municipality: B, n: null, suppressed: true}
    """),
        encoding="utf-8",
    )
    return queries.Query(folder, queries.read_yaml(folder / "query.yaml"))


def test_parameters_validated_by_type(query):
    assert queries.validate_parameters(query, {"uf": "SP"}) == {"uf": "SP"}
    with pytest.raises(queries.QueryError):
        queries.validate_parameters(query, {"uf": "SP' --"})
    with pytest.raises(queries.QueryError, match="Missing required parameter"):
        queries.validate_parameters(query, {})
    with pytest.raises(queries.QueryError, match="Undeclared parameters"):
        queries.validate_parameters(query, {"uf": "SP", "extra": "1"})


def test_test_cases_run_in_memory(query):
    assert queries.test(query) == []


def test_test_fails_when_the_result_changes(query):
    sql = query.sql.read_text().replace("count(*)", "count(*) + 1")
    query.sql.write_text(sql)
    assert queries.test(query)
