"""Eventos de linhagem no padrão OpenLineage (Job, Run, Dataset).

Cada tarefa automática de uma edição emite eventos START e COMPLETE (ou FAIL), gravados em
`<produto>/8-evaluate/data/execucoes/eventos.jsonl`. Os eventos são validados contra o JSON Schema
oficial e podem ser enviados a qualquer servidor compatível (Marquez, OpenMetadata, DataHub).

A faceta própria `statera_edition` registra produto, competência e data de corte em cada execução:
é ela que responde "de qual extração saiu este número". O namespace dos jobs é o espaço de nomes
do órgão (ex.: br.gov.estat-trabalho), declarado em statera.toml.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from statera import __version__
from statera.projeto import ESQUEMAS_PADROES, atual

SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
FACETA_EDICAO_URL = "https://example.org/statera/facetas/StateraEditionRunFacet.json"
PRODUTOR = f"https://example.org/statera/v{__version__}"


@cache
def _validador() -> Draft202012Validator:
    return Draft202012Validator(json.loads((ESQUEMAS_PADROES / "openlineage-2-0-2.json").read_text()))


def novo_run_id() -> str:
    return str(uuid.uuid4())


def dataset(nome: str, namespace: str | None = None) -> dict:
    return {"namespace": namespace or atual().namespace_dados, "name": nome}


def _agora() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def evento(tipo, job, run_id, entradas, saidas, edicao: dict, pai: dict | None = None) -> dict:
    namespace = atual().openlineage_namespace
    facetas_run = {"statera_edition": {"_producer": PRODUTOR, "_schemaURL": FACETA_EDICAO_URL, **edicao}}
    if pai:
        facetas_run["parent"] = {
            "_producer": PRODUTOR,
            "_schemaURL": "https://openlineage.io/spec/facets/1-0-1/ParentRunFacet.json",
            "run": {"runId": pai["run_id"]},
            "job": {"namespace": namespace, "name": pai["job"]},
        }
    return {
        "eventType": tipo,
        "eventTime": _agora(),
        "producer": PRODUTOR,
        "schemaURL": SCHEMA_URL,
        "run": {"runId": run_id, "facets": facetas_run},
        "job": {"namespace": namespace, "name": job},
        "inputs": entradas,
        "outputs": saidas,
    }


def validar(ev: dict) -> list[str]:
    return [e.message for e in _validador().iter_errors(ev)]


def emitir(ev: dict, arquivo: Path) -> None:
    erros = validar(ev)
    if erros:
        raise ValueError(f"Evento OpenLineage inválido: {erros}")
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


@contextmanager
def execucao(job, entradas, saidas, edicao: dict, arquivo: Path, pai: dict | None = None):
    """Emite START ao entrar e COMPLETE/FAIL ao sair. Devolve o contexto da execução."""
    run_id = novo_run_id()
    inicio = datetime.now(UTC)
    emitir(evento("START", job, run_id, entradas, saidas, edicao, pai), arquivo)
    contexto = {"run_id": run_id, "job": job, "inputs": entradas, "outputs": saidas}
    try:
        yield contexto
    except BaseException:
        emitir(evento("FAIL", job, run_id, contexto["inputs"], contexto["outputs"], edicao, pai), arquivo)
        raise
    else:
        emitir(evento("COMPLETE", job, run_id, contexto["inputs"], contexto["outputs"], edicao, pai), arquivo)
    finally:
        contexto["duracao_s"] = round((datetime.now(UTC) - inicio).total_seconds(), 2)


def ler_eventos(arquivo: Path) -> list[dict]:
    if not arquivo.exists():
        return []
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    return [json.loads(linha) for linha in linhas if linha.strip()]
