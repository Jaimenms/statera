"""Testes do repositório de dados (além da validação da plataforma)."""

from statera.projeto import atual


def test_projeto_carrega():
    assert atual().nome
