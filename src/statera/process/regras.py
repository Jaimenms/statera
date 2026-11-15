"""Regras de negócio com casos executáveis.

Cada regra vive em `<unidade>/5-process/rules/<ID>-<slug>/` (no produto interno agregado, se a
regra vale para todos os consumidores; no produto de divulgação, se define o escopo dele), com:
  - rule.md   : definição, fonte normativa, implementação
  - cases.yaml : exemplos de entrada e saída esperada (schema: statera rule-cases.schema.json)

Os casos são a fonte única. Para modelos SQL, este módulo os converte em *unit tests* do dbt, num
arquivo GERADO em `dbt/generated/_rule_unit_tests.yml`. Para modelos Python (o dbt só faz
unit tests de SQL), executa os casos chamando a função pura do modelo, `transform(entradas,
variaveis)`, com as mesmas entradas e saídas esperadas. A regra descrita para pessoas e o teste
executado pela máquina são a mesma coisa, nas duas linguagens.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from statera import gerado
from statera.projeto import Projeto, Unidade, atual, ler_yaml

CABECALHO = gerado.cabecalho(
    "statera rules sync",
    "<unidade>/5-process/rules/*/cases.yaml (um unit test por caso de cada regra)",
)


@dataclass
class Regra:
    id: str
    pasta: Path
    title: str
    modelo: str
    casos: list[dict]
    variaveis: dict
    unidade: Unidade | None = None
    requer: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.pasta.name


def arquivo_unit_tests(projeto: Projeto | None = None) -> Path:
    return (projeto or atual()).dbt_pasta / "generated" / "_rule_unit_tests.yml"


def carregar(projeto: Projeto | None = None) -> list[Regra]:
    projeto = projeto or atual()
    regras = []
    for base in projeto.pastas_regras():
        for pasta in sorted(p for p in base.iterdir() if p.is_dir()):
            casos = ler_yaml(pasta / "cases.yaml")
            md = (pasta / "rule.md").read_text(encoding="utf-8")
            regras.append(
                Regra(
                    id=casos["rule"],
                    pasta=pasta,
                    title=md.splitlines()[0].lstrip("# ").strip(),
                    modelo=casos["model"],
                    casos=casos["cases"],
                    variaveis=casos.get("variables", {}),
                    unidade=projeto.unidade_de(pasta),
                    requer=list(casos.get("requires", [])),
                )
            )
    return sorted(regras, key=lambda r: r.id)


def _nome_teste(regra: Regra, caso: dict) -> str:
    return re.sub(r"[^a-z0-9_]", "_", f"{regra.id}__{caso['id']}".lower())


def modelo_python(projeto: Projeto, nome: str) -> Path | None:
    """O arquivo do modelo Python do dbt com esse nome, se houver (`<unidade>/5-process/models/**/<nome>.py`)."""
    for u in projeto.produtos():
        if achados := sorted((u.fase(5) / "models").rglob(f"{nome}.py")):
            return achados[0]
    return None


def gerar_unit_tests(projeto: Projeto | None = None) -> str:
    projeto = projeto or atual()
    unit_tests = []
    for regra in carregar(projeto):
        if modelo_python(projeto, regra.modelo):
            continue  # o dbt não faz unit test de modelo Python: os casos rodam em testar_casos_python
        for caso in regra.casos:
            teste = {
                "name": _nome_teste(regra, caso),
                "description": f"{regra.id}: {caso['description']}",
                "model": regra.modelo,
                "given": [{"input": f"ref('{entrada}')", "rows": linhas} for entrada, linhas in caso["inputs"].items()],
                "expect": {"rows": caso["expected"]},
                "config": {"meta": {"rule": regra.id, "caso": caso["id"]}},
            }
            if regra.variaveis:
                teste["overrides"] = {"vars": regra.variaveis}
            unit_tests.append(teste)
    corpo = yaml.safe_dump({"version": 2, "unit_tests": unit_tests}, allow_unicode=True, sort_keys=False, width=110)
    return CABECALHO + corpo


def sincronizar(projeto: Projeto | None = None) -> Path:
    destino = arquivo_unit_tests(projeto)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar_unit_tests(projeto), encoding="utf-8")
    return destino


def esta_sincronizado(projeto: Projeto | None = None) -> bool:
    destino = arquivo_unit_tests(projeto)
    return destino.exists() and destino.read_text(encoding="utf-8") == gerar_unit_tests(projeto)


# ---------------------------------------------------------------------------
# Casos de regras implementadas em modelos Python
# ---------------------------------------------------------------------------


@dataclass
class ResultadoCaso:
    regra: str
    caso: str
    situacao: str  # "ok", "failed" ou "not-run"
    mensagem: str = ""


def _funcao_transformar(arquivo: Path):
    nome = f"statera_modelo_{arquivo.stem}"
    spec = importlib.util.spec_from_file_location(nome, arquivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    if not hasattr(modulo, "transform"):
        raise AttributeError(f"{arquivo.name} não define transform(entradas, variaveis)")
    return modulo.transform


def _normalizar(valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    if hasattr(valor, "item"):
        valor = valor.item()
    if isinstance(valor, float):
        return round(valor, 6) if not valor.is_integer() else int(valor)
    if hasattr(valor, "isoformat"):
        return valor.isoformat()[:10] if len(valor.isoformat()) >= 10 else valor.isoformat()
    return valor


def _comparar(obtido, esperado: list[dict]) -> str:
    """Igual ao unit test do dbt: as mesmas linhas, nas colunas do esperado, sem importar a ordem."""
    colunas = list(dict.fromkeys(c for linha in esperado for c in linha))
    faltam = [c for c in colunas if c not in obtido.columns]
    if faltam:
        return f"colunas ausentes na saída: {faltam}"
    linhas_obtidas = sorted(
        (tuple(_normalizar(v) for v in linha) for linha in obtido[colunas].itertuples(index=False)), key=repr
    )
    linhas_esperadas = sorted((tuple(_normalizar(linha.get(c)) for c in colunas) for linha in esperado), key=repr)
    if linhas_obtidas != linhas_esperadas:
        return f"esperado {linhas_esperadas}, obtido {linhas_obtidas} (colunas {colunas})"
    return ""


def testar_casos_python(projeto: Projeto | None = None) -> list[ResultadoCaso]:
    """Executa os casos das regras cujo modelo é Python. Casos que exigem uma ferramenta ausente
    (pspp, r) ficam "not-run", exceto com STATERA_EXIGIR=<ferramentas> (o CI), em que falham."""
    import pandas as pd

    from statera.legacy import pspp, r

    projeto = projeto or atual()
    disponiveis = {"pspp": pspp.available(), "r": r.available()}
    exigidas = {f.strip() for f in os.environ.get("STATERA_EXIGIR", "").split(",") if f.strip()}
    resultados = []
    for regra in carregar(projeto):
        arquivo = modelo_python(projeto, regra.modelo)
        if arquivo is None:
            continue
        ausentes = [f for f in regra.requer if not disponiveis.get(f)]
        if ausentes:
            situacao = "failed" if set(ausentes) & exigidas else "not-run"
            resultados += [
                ResultadoCaso(regra.id, c["id"], situacao, f"requer {', '.join(ausentes)}, não instalado")
                for c in regra.casos
            ]
            continue
        try:
            transform = _funcao_transformar(arquivo)
        except Exception as erro:  # noqa: BLE001 - qualquer erro de carga é falha do caso
            resultados += [ResultadoCaso(regra.id, c["id"], "failed", str(erro)) for c in regra.casos]
            continue
        for caso in regra.casos:
            entradas = {nome: pd.DataFrame(linhas) for nome, linhas in caso["inputs"].items()}
            try:
                diferenca = _comparar(transform(entradas, dict(regra.variaveis)), caso["expected"])
            except Exception as erro:  # noqa: BLE001
                diferenca = f"{type(erro).__name__}: {erro}"
            resultados.append(ResultadoCaso(regra.id, caso["id"], "failed" if diferenca else "ok", diferenca))
    return resultados
