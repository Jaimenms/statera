"""Autorização de equipes (papéis) a produtos internos: finalidade, ato e prazo de validade."""

from datetime import date

import pytest

from statera import portas, validacao
from statera.consume import catalog
from statera.projeto import atual


class _Interno:
    nome = "internal"
    interno = True

    def __init__(self, itens):
        self._itens = itens

    def propriedade(self, nome, padrao=None):
        return self._itens if nome == "authorizedConsumers" else padrao


def test_autorizacao_vale_ate_a_data():
    u = _Interno([{"role": "equipe-x", "purpose": "study", "until": "2026-12-31", "act": "Despacho 1"}])
    assert portas.autorizacao_vigente(u, "equipe-x", hoje=date(2026, 12, 31))
    assert portas.autorizacao_vigente(u, "equipe-x", hoje=date(2027, 1, 1)) is None
    assert portas.consumidores_autorizados(u) == {}  # papel não se confunde com produto ou consulta


def test_validacao_barra_autorizacao_vencida_ou_incompleta(projeto_minimo):
    u = _Interno(
        [
            {"role": "equipe-x", "purpose": "study", "until": "2020-01-01", "act": "Despacho 1"},
            {"role": "nao-existe", "until": "2099-01-01"},
        ]
    )
    problemas = validacao._problemas_papeis(atual(), u)
    assert any("venceu em 2020-01-01" in p for p in problemas)
    assert any("'nao-existe': papel não existe" in p for p in problemas)
    assert any("falta 'act'" in p for p in problemas) and any("falta 'purpose'" in p for p in problemas)


def test_biblioteca_nega_produto_interno_sem_autorizacao(projeto_minimo):
    catalog.open(role="equipe-x")._autorizar("bronze.registros")  # autorizada até 2099
    with pytest.raises(catalog.AccessDenied):
        catalog.open(role="outra-equipe")._autorizar("bronze.registros")
    catalog.open()._autorizar("bronze.registros")  # sem papel: equipe de dados (só aviso)
