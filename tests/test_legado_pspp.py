"""Sintaxe SPSS legada pelo PSPP: a ligação (entradas .sav, FILE HANDLE, erros, saída) é testada com um
PSPP simulado em qualquer máquina; a sintaxe de verdade, só onde o PSPP existe (o CI o instala)."""

import shutil
import sys
import textwrap

import pandas as pd
import pytest

from statera.legacy import pspp

PSPP_SIMULADO = textwrap.dedent(
    """
    import re, shutil, sys
    if "--version" in sys.argv:
        print("pspp (simulado) 0.0"); sys.exit(0)
    script = open(sys.argv[-1], encoding="utf-8").read()
    if "ERRO_SIMULADO" in script:
        print("executar.sps:5: error: SIMULADO: erro de sintaxe"); sys.exit(0)
    handles = dict(re.findall(r"FILE HANDLE (\\w+) /NAME='([^']+)'", script))
    origem = re.search(r"GET FILE=(\\w+)", script).group(1)
    destino = re.search(r"SAVE OUTFILE=(\\w+)", script).group(1)
    shutil.copy(handles[origem], handles[destino])  # o simulado só copia: não interpreta SPSS
    """
)


@pytest.fixture
def simulado(tmp_path, monkeypatch):
    stub = tmp_path / "pspp_simulado.py"
    stub.write_text(PSPP_SIMULADO, encoding="utf-8")
    monkeypatch.setenv("STATERA_PSPP", f"{sys.executable} {stub}")
    sintaxe = tmp_path / "copia.sps"
    sintaxe.write_text("GET FILE=registros.\nSAVE OUTFILE=resultado.\n", encoding="utf-8")
    return sintaxe


def test_ida_e_volta_pelo_sav(simulado):
    df = pd.DataFrame({"sigla_uf": ["SP", "RJ"], "registros": [3, 5]})
    saida = pspp.run(simulado, {"registros": df})
    assert saida["sigla_uf"].tolist() == ["SP", "RJ"] and saida["registros"].tolist() == [3, 5]
    assert pspp.version() == "pspp (simulado) 0.0"


def test_erro_na_listagem_vira_excecao(simulado, tmp_path):
    ruim = tmp_path / "ruim.sps"
    ruim.write_text("* ERRO_SIMULADO.\nGET FILE=registros.\nSAVE OUTFILE=resultado.\n", encoding="utf-8")
    with pytest.raises(pspp.PSPPError, match="SIMULADO"):
        pspp.run(ruim, {"registros": pd.DataFrame({"a": [1]})})


def test_sem_pspp_ou_com_handle_invalido(monkeypatch, tmp_path):
    with pytest.raises(pspp.PSPPError, match="handle inválido"):
        pspp.run(tmp_path / "x.sps", {"1registros": pd.DataFrame({"a": [1]})})
    monkeypatch.setenv("STATERA_PSPP", "comando-que-nao-existe")
    assert not pspp.available()
    with pytest.raises(pspp.PSPPError, match="não encontrado"):
        pspp.run(tmp_path / "x.sps", {"registros": pd.DataFrame({"a": [1]})})


@pytest.mark.skipif(shutil.which("pspp") is None, reason="GNU PSPP não instalado (o CI o instala)")
def test_sintaxe_real_no_pspp(tmp_path, monkeypatch):
    monkeypatch.delenv("STATERA_PSPP", raising=False)
    sintaxe = tmp_path / "agrega.sps"
    sintaxe.write_text(
        "GET FILE=registros.\n"
        "AGGREGATE OUTFILE=* /BREAK=sigla_uf /registros=N /valor_total=SUM(valor).\n"
        "COMPUTE valor_medio = valor_total / registros.\n"
        "SAVE OUTFILE=resultado.\n",
        encoding="utf-8",
    )
    df = pd.DataFrame({"sigla_uf": ["SP", "SP", "RJ"], "valor": [10.0, 30.0, 5.0]})
    saida = pspp.run(sintaxe, {"registros": df}).sort_values("sigla_uf")
    assert saida["registros"].tolist() == [1, 2] and saida["valor_medio"].tolist() == [5.0, 20.0]
