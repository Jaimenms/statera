"""Script R legado executado pelo Rscript, chamado de um modelo Python do dbt.

Mesma estrutura do PSPP (`statera.legacy.pspp`), para que um script `.R` legado rode dentro do
processo sem ser reescrito:

    # products/<p>/5-process/models/marts/<p>/<modelo>.py
    from pathlib import Path
    from statera.legacy import r

    SCRIPT = Path(__file__).with_name("<modelo>.R")

    def transform(inputs, variaveis):
        return r.run(SCRIPT, {"registros": entradas["int_x__registros"]}, saida="resultado")

    def model(dbt, session):
        return transform({"int_x__registros": dbt.ref("int_x__registros").df()}, {})

A única adaptação do script legado é trocar os caminhos de arquivo pelos que a plataforma define
no início do script: cada entrada vira um `.rds` em `entradas$<nome>` (`readRDS(entradas$registros)`)
e o resultado é gravado em `saida` (`saveRDS(resultado, saida)`). Só R base: nenhum pacote é exigido
pela plataforma; os que o script usa são dele e precisam estar instalados no ambiente.

O comando é `Rscript --vanilla` (sem perfil nem área de trabalho salvos, para que a execução não
dependa da máquina), ou o que a variável STATERA_R disser (ex.: um contêiner com versão fixada).
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

NOME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class RError(RuntimeError):
    """O R não está disponível ou o script terminou com erro."""


def comando() -> list[str]:
    return shlex.split(os.environ.get("STATERA_R", "Rscript"))


def available() -> bool:
    return shutil.which(comando()[0]) is not None


def version() -> str:
    if not available():
        return "indisponível"
    processo = subprocess.run([*comando(), "--version"], capture_output=True, text=True, timeout=60)
    texto = (processo.stdout or processo.stderr).strip()  # o Rscript escreve a versão no stderr
    return texto.splitlines()[0] if processo.returncode == 0 and texto else "?"


def _texto_r(caminho: Path) -> str:
    return '"' + str(caminho).replace("\\", "\\\\").replace('"', '\\"') + '"'


def run(script: Path, entradas: dict[str, pd.DataFrame], saida: str = "resultado", timeout: int = 900):
    """Roda o script R com as entradas (DataFrames) e devolve o data.frame gravado em `saida`."""
    import pyreadr

    for nome in [*entradas, saida]:
        if not NOME.match(nome):
            raise RError(f"nome inválido para o R: {nome!r}")
    if not available():
        raise RError(
            f"R não encontrado ({comando()[0]}). Instale o R (apt install r-base-core; brew install r) ou "
            "aponte STATERA_R para o comando."
        )
    with tempfile.TemporaryDirectory(prefix="statera-r-") as pasta:
        pasta = Path(pasta)
        for nome, df in entradas.items():
            pyreadr.write_rds(str(pasta / f"{nome}.rds"), df)
        arquivo_saida = pasta / f"{saida}.rds"
        cabecalho = [
            "# Definido pela plataforma Statera: entradas (.rds) e arquivo de saída.",
            "entradas <- list(",
            ",\n".join(f"  {nome} = {_texto_r(pasta / f'{nome}.rds')}" for nome in entradas),
            ")",
            f"saida <- {_texto_r(arquivo_saida)}",
        ]
        executavel = pasta / "executar.R"
        executavel.write_text("\n".join(cabecalho) + "\n" + Path(script).read_text(encoding="utf-8"), encoding="utf-8")
        try:
            processo = subprocess.run(
                [*comando(), "--vanilla", str(executavel)], capture_output=True, text=True, timeout=timeout, cwd=pasta
            )
        except subprocess.TimeoutExpired as erro:
            raise RError(f"{Path(script).name}: R excedeu {timeout} s") from erro
        if processo.returncode != 0 or not arquivo_saida.exists():
            detalhe = (processo.stderr or processo.stdout)[-1500:].strip()
            raise RError(f"{Path(script).name}: R terminou com erro (código {processo.returncode}):\n  {detalhe}")
        return next(iter(pyreadr.read_r(str(arquivo_saida)).values()))
