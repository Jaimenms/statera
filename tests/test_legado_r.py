"""Script R legado: a ligação (entradas .rds, variáveis entradas/saida, erros, saída) é testada com um
Rscript simulado em qualquer máquina; o R de verdade, só onde ele existe (o CI o instala)."""

import shutil
import sys
import textwrap

import pandas as pd
import pytest

from statera.legacy import r

R_SIMULADO = textwrap.dedent(
    """
    import re, shutil, sys
    if "--version" in sys.argv:
        print("Rscript (R) version 0.0 (simulado)", file=sys.stderr); sys.exit(0)
    script = open(sys.argv[-1], encoding="utf-8").read()
    if "stop(" in script:
        print("Error: erro simulado no script", file=sys.stderr); sys.exit(1)
    entradas = dict(re.findall(r'^  (\\w+) = "([^"]+)"', script, re.M))
    saida = re.search(r'^saida <- "([^"]+)"', script, re.M).group(1)
    origem = re.search(r"readRDS\\(entradas\\$(\\w+)\\)", script).group(1)
    shutil.copy(entradas[origem], saida)  # o simulado só copia: não interpreta R
    """
)


@pytest.fixture
def simulado(tmp_path, monkeypatch):
    stub = tmp_path / "rscript_simulado.py"
    stub.write_text(R_SIMULADO, encoding="utf-8")
    monkeypatch.setenv("STATERA_R", f"{sys.executable} {stub}")
    script = tmp_path / "copia.R"
    script.write_text("registros <- readRDS(entradas$registros)\nsaveRDS(registros, saida)\n", encoding="utf-8")
    return script


def test_ida_e_volta_pelo_rds(simulado):
    df = pd.DataFrame({"sigla_uf": ["SP", "RJ"], "registros": [3, 5]})
    saida = r.run(simulado, {"registros": df})
    assert saida["sigla_uf"].tolist() == ["SP", "RJ"] and saida["registros"].tolist() == [3, 5]
    assert r.version() == "Rscript (R) version 0.0 (simulado)"


def test_erro_do_r_vira_excecao(simulado, tmp_path):
    ruim = tmp_path / "ruim.R"
    ruim.write_text('registros <- readRDS(entradas$registros)\nstop("falhou")\n', encoding="utf-8")
    with pytest.raises(r.RError, match="erro simulado"):
        r.run(ruim, {"registros": pd.DataFrame({"a": [1]})})


def test_sem_r_ou_com_nome_invalido(monkeypatch, tmp_path):
    with pytest.raises(r.RError, match="nome inválido"):
        r.run(tmp_path / "x.R", {"1registros": pd.DataFrame({"a": [1]})})
    monkeypatch.setenv("STATERA_R", "comando-que-nao-existe")
    assert not r.available()
    with pytest.raises(r.RError, match="não encontrado"):
        r.run(tmp_path / "x.R", {"registros": pd.DataFrame({"a": [1]})})


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R não instalado (o CI o instala)")
def test_script_real_no_r(tmp_path, monkeypatch):
    monkeypatch.delenv("STATERA_R", raising=False)
    script = tmp_path / "mediana.R"
    script.write_text(
        "registros <- readRDS(entradas$registros)\n"
        "resultado <- aggregate(valor ~ sigla_uf, data = registros, FUN = median)\n"
        "saveRDS(resultado, saida)\n",
        encoding="utf-8",
    )
    df = pd.DataFrame({"sigla_uf": ["SP", "SP", "SP", "RJ"], "valor": [10.0, 30.0, 20.0, 5.0]})
    saida = r.run(script, {"registros": df}).sort_values("sigla_uf")
    assert saida["value"].tolist() == [5.0, 20.0]
