"""Séries de publicação e registro edição a edição (GSBPM 7).

Duas coisas diferentes, guardadas em lugares diferentes:

* **Série** (estável, versionada no repositório de conteúdo): `<produto>/7-disseminate/series.yaml`
  descreve o que cada edição publica, a licença, as políticas de revisão e de retificação, a porta
  principal e onde as edições são registradas. Só muda quando a série muda.

* **Edições** (crescem a cada período, FORA do repositório de conteúdo): no registro declarado em
  statera.toml ([registro] edicoes), um `datapackage.json` por competência e um índice
  `editions.csv`. Em produção, outro repositório git escrito por commit automático, ou um
  armazenamento com versionamento. Publicar uma edição não exige commit no código.
"""

from __future__ import annotations

import csv
import json
from functools import cache
from pathlib import Path

import pandas as pd
from jsonschema import Draft7Validator

from statera.projeto import ESQUEMAS_PADROES, Projeto, Unidade, atual, ler_yaml

COLUNAS_INDICE = [
    "period",
    "version",
    "cut_date",
    "published_at",
    "code_version",
    "run",
    "files",
    "suppressed_cells",
    "corrects_version",
    "main_resource",
    "sha256_recurso_principal",
]


def _readme_raiz(projeto: Projeto) -> str:
    return f"""# Registro das edições publicadas — {projeto.orgao or projeto.nome}

Este repositório é escrito **pelo processo de produção** (tarefa de publicação do processo to-be,
executada por `statera`), por commit automático, a cada edição publicada. Não se edita à mão. O
código, os contratos e o desenho das séries ficam no repositório de conteúdo (`{projeto.raiz.name}`);
aqui fica só o histórico de edições:

| Arquivo | Conteúdo |
|---|---|
| `<serie>/<competência>.datapackage.json` | Data Package da edição: arquivos, hashes, data de corte |
| `<serie>/editions.csv` | Índice da série: uma linha por edição (e por retificação) |
| `<serie>/evaluations/<competência>.json` e `.md` | Indicadores de qualidade da edição (GSBPM 8) |
| `<serie>/README.md` | Página gerada: edições, revisões, linhagem por versão, duração |

Os arquivos de dados publicados ficam no armazenamento de publicação (portal, dados.gov.br); aqui
ficam os metadados e os hashes que provam o que foi publicado.
""" + ("\n> Demonstração com DADOS SINTÉTICOS.\n" if projeto.sintetico else "")


@cache
def _validador_datapackage() -> Draft7Validator:
    return Draft7Validator(json.loads((ESQUEMAS_PADROES / "datapackage-2.0.json").read_text()))


def arquivo_serie(unidade: Unidade) -> Path:
    return unidade.fase(7) / "series.yaml"


def series(projeto: Projeto | None = None) -> list[tuple[Unidade, dict]]:
    projeto = projeto or atual()
    return [(u, ler_yaml(arquivo_serie(u))) for u in projeto.produtos() if u.tem("7-disseminate/series.yaml")]


def serie(unidade: Unidade) -> dict:
    return ler_yaml(arquivo_serie(unidade))


def caminho_edicao(unidade: Unidade, competencia: str) -> Path:
    padrao = serie(unidade)["edition_registry"]["edition"]
    return unidade.projeto.registro_edicoes / padrao.format(competencia=competencia)


def caminho_indice(unidade: Unidade) -> Path:
    return unidade.projeto.registro_edicoes / serie(unidade)["edition_registry"]["index"]


def pasta_avaliacoes(unidade: Unidade) -> Path:
    return caminho_indice(unidade).parent / "evaluations"


def edicoes(unidade: Unidade) -> list[dict]:
    """Data Packages registrados da série, em ordem de competência."""
    pasta = caminho_edicao(unidade, "X").parent
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pasta.glob("*.datapackage.json"))]


def publicada(unidade: Unidade, competencia: str) -> bool:
    return caminho_edicao(unidade, competencia).exists()


def versao_e_retificacao(unidade: Unidade, competencia: str) -> tuple[str, dict | None]:
    """Versão do novo registro; se a edição já existe, é retificação e a versão sobe."""
    existente = caminho_edicao(unidade, competencia)
    if not existente.exists():
        return "1.0.0", None
    anterior = json.loads(existente.read_text(encoding="utf-8"))
    maior, menor, _ = (int(x) for x in anterior["version"].split("."))
    return f"{maior}.{menor + 1}.0", {"version": anterior["version"], "publishedAt": anterior["created"]}


def registrar(unidade: Unidade, datapackage: dict) -> Path:
    erros = [e.message for e in _validador_datapackage().iter_errors(datapackage)]
    if erros:
        raise ValueError(f"Data Package inválido, edição não registrada: {erros[:3]}")
    raiz = unidade.projeto.registro_edicoes
    raiz.mkdir(parents=True, exist_ok=True)
    if not (raiz / "README.md").exists():
        (raiz / "README.md").write_text(_readme_raiz(unidade.projeto), encoding="utf-8")
    destino = caminho_edicao(unidade, datapackage["x-statera"]["period"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(datapackage, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    reconstruir_indice(unidade)
    return destino


def _linha_indice(dp: dict, principal: str) -> dict:
    x = dp["x-statera"]
    recurso = next((r for r in dp["resources"] if r["name"] == principal), {})
    return {
        "period": x["period"],
        "version": dp["version"],
        "cut_date": x["cutDate"],
        "published_at": dp["created"],
        "code_version": x["codeVersion"],
        "run": x.get("run") or "",
        "files": len(dp["resources"]),
        "suppressed_cells": sum((r.get("x-statera") or {}).get("suppressedCells", 0) for r in dp["resources"]),
        "corrects_version": (x.get("corrects") or {}).get("version", ""),
        "main_resource": principal,
        "sha256_recurso_principal": recurso.get("hash", "").removeprefix("sha256:"),
    }


def reconstruir_indice(unidade: Unidade) -> Path:
    destino = caminho_indice(unidade)
    destino.parent.mkdir(parents=True, exist_ok=True)
    principal = serie(unidade)["main_resource"]
    with destino.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUNAS_INDICE)
        escritor.writeheader()
        escritor.writerows(_linha_indice(dp, principal) for dp in edicoes(unidade))
    return destino


def competencia_anterior_publicada(unidade: Unidade, competencia: str) -> str | None:
    anteriores = [dp["x-statera"]["period"] for dp in edicoes(unidade) if dp["x-statera"]["period"] < competencia]
    return anteriores[-1] if anteriores else None


def tabela_publicada(unidade: Unidade, competencia: str, porta: str) -> pd.DataFrame | None:
    """Tabela de uma porta como foi publicada na edição (pasta de publicação da fase 7)."""
    nome = serie(unidade)["files_per_edition"]["tables"].format(porta=porta)
    caminho = unidade.dados(7) / competencia / nome
    return pd.read_csv(caminho, dtype={unidade.projeto.convencoes["period_column"]: str}) if caminho.exists() else None


def tabela_anterior(unidade: Unidade, competencia: str, porta: str) -> pd.DataFrame | None:
    """A mesma porta na edição publicada imediatamente anterior (controles e sumário)."""
    anterior = competencia_anterior_publicada(unidade, competencia)
    return tabela_publicada(unidade, anterior, porta) if anterior else None
