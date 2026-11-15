"""Sintaxe SPSS legada executada pelo GNU PSPP, chamada de um modelo Python do dbt.

Para que um script `.sps` legado rode dentro do processo sem ser reescrito:

    # products/<p>/5-process/models/marts/<p>/<modelo>.py
    from pathlib import Path
    from statera.legacy import pspp

    SINTAXE = Path(__file__).with_name("<modelo>.sps")

    def transform(inputs, variaveis):
        return pspp.run(SINTAXE, {"registros": entradas["int_x__registros"]}, saida="resultado")

    def model(dbt, session):
        return transform({"int_x__registros": dbt.ref("int_x__registros").df()}, {})

A única adaptação do script legado é trocar os caminhos de arquivo por FILE HANDLEs, que a
plataforma define: cada entrada vira um arquivo `.sav` com o nome dela (`GET FILE=registros.`) e
o resultado é lido do handle de saída (`SAVE OUTFILE=resultado.`). O resto da sintaxe não muda.

O PSPP não é o SPSS: cada script precisa de evidência de que o PSPP o executa como o SPSS antes de
servir de referência (certificação contra o SPSS ou contra edições publicadas; fundamentos, §PSPP).
O comando é `pspp`, ou o que a variável STATERA_PSPP disser (ex.: um contêiner).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

NOME_HANDLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ERRO = re.compile(r"\berror:", re.IGNORECASE)


class PSPPError(RuntimeError):
    """O PSPP não está disponível ou a sintaxe terminou com erro."""


def comando() -> list[str]:
    return shlex.split(os.environ.get("STATERA_PSPP", "pspp"))


def available() -> bool:
    return shutil.which(comando()[0]) is not None


def version() -> str:
    if not available():
        return "indisponível"
    processo = subprocess.run([*comando(), "--version"], capture_output=True, text=True, timeout=60)
    return (processo.stdout or processo.stderr).splitlines()[0].strip() if processo.returncode == 0 else "?"


def _aspas(caminho: Path) -> str:
    return "'" + str(caminho).replace("'", "''") + "'"


def run(sintaxe: Path, entradas: dict[str, pd.DataFrame], saida: str = "resultado", timeout: int = 900):
    """Roda a sintaxe no PSPP com as entradas (DataFrames) e devolve o dataset salvo em `saida`."""
    import pyreadstat

    for nome in [*entradas, saida]:
        if not NOME_HANDLE.match(nome):
            raise PSPPError(f"nome de handle inválido para o SPSS: {nome!r}")
    if not available():
        raise PSPPError(
            f"PSPP não encontrado ({comando()[0]}). Instale o GNU PSPP (apt install pspp) ou aponte "
            "STATERA_PSPP para o comando."
        )
    with tempfile.TemporaryDirectory(prefix="statera-pspp-") as pasta:
        pasta = Path(pasta)
        for nome, df in entradas.items():
            pyreadstat.write_sav(df, str(pasta / f"{nome}.sav"))
        handles = [f"FILE HANDLE {nome} /NAME={_aspas(pasta / f'{nome}.sav')}." for nome in [*entradas, saida]]
        script = pasta / "executar.sps"
        script.write_text("\n".join(handles) + "\n" + Path(sintaxe).read_text(encoding="utf-8"), encoding="utf-8")
        listagem = pasta / "listagem.txt"
        try:
            processo = subprocess.run(
                [*comando(), "-o", str(listagem), str(script)], capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as erro:
            raise PSPPError(f"{Path(sintaxe).name}: PSPP excedeu {timeout} s") from erro
        texto = processo.stdout + processo.stderr + (listagem.read_text(errors="replace") if listagem.exists() else "")
        erros = [linha.strip() for linha in texto.splitlines() if ERRO.search(linha)]
        arquivo_saida = pasta / f"{saida}.sav"
        if processo.returncode != 0 or erros or not arquivo_saida.exists():
            detalhe = "\n  ".join(erros[:10]) or texto[-1500:]
            raise PSPPError(
                f"{Path(sintaxe).name}: PSPP terminou com erro (código {processo.returncode}):\n  {detalhe}"
            )
        dados, _ = pyreadstat.read_sav(str(arquivo_saida))
        return dados
