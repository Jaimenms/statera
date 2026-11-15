"""Extração incremental: sistema de origem -> camada bronze, dirigida pelos contratos (GSBPM 4.3).

As fontes não estão no código: cada contrato do bronze declara de onde vem e como datar a extração.

    customProperties:
      - property: extracao
        value: {origem: esocial_eventos.parquet, colunaRecepcao: data_recepcao, sistemaOrigem: eSocial}

Cada execução grava um LOTE imutável com o que foi recebido desde a última carga
(`bronze/<tabela>/batch=000001/parte-0.parquet` + `_metadados.json` com a janela de recepção, linhas e
hash). Cada registro fica guardado uma vez. Um CORTE não é uma cópia: é uma marca sobre os lotes,
"recebido até a data de corte e presente até o lote L", registrada num manifesto imutável. Assim um
lote que chegue atrasado não muda um corte já marcado, e qualquer data pode ser reconstruída.

Na demonstração, a "source" é um Parquet em `<produto>/4-collect/data/source/`; em produção, um
conector para o sistema de origem e, para o armazenamento, tabelas Apache Iceberg com tags.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

from statera import contratos
from statera.edicao import agora
from statera.projeto import Projeto, Unidade, atual


@dataclass(frozen=True)
class Fonte:
    contrato_id: str
    tabela: str  # nome da tabela no schema bronze
    origem: str
    coluna_recepcao: str
    sistema: str
    descricao: str
    produto: Unidade

    @property
    def arquivo_origem(self) -> Path:
        return self.produto.dados(4) / "source" / self.origem

    @property
    def pasta_bronze(self) -> Path:
        return self.produto.dados(4) / "bronze" / self.tabela

    def lotes(self) -> list[dict]:
        """Metadados dos lotes já extraídos, em ordem."""
        arquivos = sorted(self.pasta_bronze.glob("batch=*/_metadados.json"))
        return [json.loads(a.read_text(encoding="utf-8")) for a in arquivos]

    def arquivo_lote(self, numero: int) -> Path:
        return self.pasta_bronze / f"batch={numero:06d}" / "parte-0.parquet"


def fontes(projeto: Projeto | None = None, unidade: Unidade | None = None) -> list[Fonte]:
    """Fontes declaradas nos contratos do bronze (de todo o projeto, ou de um produto de fonte)."""
    projeto = projeto or atual()
    saida = []
    for caminho, contrato in contratos.todos(projeto).values():
        extracao = contratos.propriedade_custom(contrato, "extraction")
        dono = projeto.unidade_de(caminho)
        if contratos.camada(contrato) != "bronze" or not extracao or (unidade and dono != unidade):
            continue
        saida.append(
            Fonte(
                contrato_id=contrato["id"],
                tabela=contratos.tabela_fisica(contrato).split(".")[-1],
                origem=extracao["source"],
                coluna_recepcao=extracao["receivedAtColumn"],
                sistema=extracao["sourceSystem"],
                descricao=contrato["name"],
                produto=dono,
            )
        )
    return sorted(saida, key=lambda f: f.tabela)


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def extrair_incremento(data_corte: date, fontes_: list[Fonte]) -> list[dict]:
    """Novo lote com o que foi recebido entre a última carga e a data de corte (se houver)."""
    resultados = []
    con = duckdb.connect()
    try:
        for fonte in fontes_:
            if not fonte.arquivo_origem.exists():
                raise FileNotFoundError(f"Origem {fonte.arquivo_origem} não encontrada. Rode antes: statera synthetic")
            lotes = fonte.lotes()
            marca = max((date.fromisoformat(m["window_end"]) for m in lotes), default=None)
            if marca is not None and data_corte <= marca:
                resultados.append({"source": fonte.tabela, "batch": None, "rows": 0, "marca_dagua": marca.isoformat()})
                continue
            numero = len(lotes) + 1
            arquivo = fonte.arquivo_lote(numero)
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            col = f"CAST({fonte.coluna_recepcao} AS DATE)"
            janela = f"{col} <= DATE '{data_corte.isoformat()}'"
            if marca is not None:
                janela += f" AND {col} > DATE '{marca.isoformat()}'"
            con.execute(
                f"COPY (SELECT *, CAST({numero} AS INTEGER) AS batch FROM read_parquet('{fonte.arquivo_origem}') "
                f"WHERE {janela}) TO '{arquivo}' (FORMAT parquet)"
            )
            meta = {
                "source": fonte.tabela,
                "contract": fonte.contrato_id,
                "sourceSystem": fonte.sistema,
                "batch": numero,
                "window_start": marca.isoformat() if marca else None,
                "window_end": data_corte.isoformat(),
                "rows": con.execute(f"SELECT count(*) FROM read_parquet('{arquivo}')").fetchone()[0],
                "sha256": _sha256(arquivo),
                "extracted_at": agora(),
            }
            (arquivo.parent / "_metadados.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            resultados.append(meta)
    finally:
        con.close()
    return resultados


def manifesto_das_fontes(data_corte: date, fontes_: list[Fonte]) -> dict:
    """Para cada fonte: o lote máximo do corte, as linhas no corte e os hashes dos lotes usados."""
    saida = {}
    con = duckdb.connect()
    try:
        for fonte in fontes_:
            lotes = fonte.lotes()
            cobrem = [m for m in lotes if date.fromisoformat(m["window_end"]) >= data_corte]
            if not cobrem:
                raise ValueError(f"{fonte.tabela}: nenhum lote cobre a data de corte {data_corte} (extraia antes)")
            lote_max = min(m["batch"] for m in cobrem)
            usados = [m for m in lotes if m["batch"] <= lote_max]
            arquivos = ", ".join(f"'{fonte.arquivo_lote(m['batch'])}'" for m in usados)
            linhas = con.execute(
                f"SELECT count(*) FROM read_parquet([{arquivos}]) "
                f"WHERE CAST({fonte.coluna_recepcao} AS DATE) <= DATE '{data_corte.isoformat()}'"
            ).fetchone()[0]
            assinatura = hashlib.sha256("".join(m["sha256"] for m in usados).encode()).hexdigest()
            saida[fonte.tabela] = {
                "contract": fonte.contrato_id,
                "sourceSystem": fonte.sistema,
                "cutDate": data_corte.isoformat(),
                "batch": lote_max,
                "rows": linhas,
                "batches": [{"batch": m["batch"], "sha256": m["sha256"]} for m in usados],
                "sha256": assinatura,
            }
    finally:
        con.close()
    return saida


def publicar_bronze_no_warehouse(fontes_: list[Fonte], projeto: Projeto | None = None) -> None:
    """Expõe todos os lotes do bronze como views no schema `bronze` do motor (sem copiar o dado)."""
    projeto = projeto or atual()
    projeto.warehouse.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(projeto.warehouse))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        for fonte in fontes_:
            padrao = fonte.pasta_bronze / "batch=*" / "parte-0.parquet"
            con.execute(
                f"CREATE OR REPLACE VIEW bronze.{fonte.tabela} AS "
                f"SELECT * FROM read_parquet('{padrao}', union_by_name = true, hive_partitioning = false)"
            )
    finally:
        con.close()
