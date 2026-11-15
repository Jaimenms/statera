"""Biblioteca de consulta a todas as camadas do medalhão, para quem analisa os dados.

    from statera.consume import catalog

    cat = catalog.open()
    cat.search(theme="emprego-formal", layer="gold")      # descoberta pelos eixos do edital
    print(cat.describe("gold.caged_saldo_uf_mensal"))     # propósito, colunas, limitações, classificação

    t = cat.table("gold.caged_saldo_uf_mensal")            # modo VIGENTE: tabela Ibis (preguiçosa)
    t.filter(t.competencia >= "2026-01").group_by("regiao").agg(saldo=t.saldo.sum()).to_pandas()

    cat.table("bronze.esocial_eventos", cut_date="2026-07-20")     # modo DATA DE CORTE
    ed = cat.edition("novo-caged", "2026-06")                       # modo EDIÇÃO PUBLICADA
    ed.table("saldo-uf"); ed.provenance                          # hash conferido

    cat.metric("saldo", by=["regiao"], period="2026-06")
    cat.query("serie-saldo-uf", uf="SP", competencia_inicio="2026-01", competencia_fim="2026-06")

Ibis traduz o mesmo código para o SQL de outros motores (PostgreSQL, Trino, Spark): a análise
escrita no notebook com DuckDB roda no servidor sem reescrita. A classificação do contrato é
MOSTRADA e gera aviso, mas a proteção de verdade é do motor (permissões geradas dos contratos):
a biblioteca é conveniência, não fronteira de segurança.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import ibis
import pandas as pd

from statera import classificacao, contratos
from statera.consume import metrics, queries
from statera.disseminate import registro
from statera.projeto import Projeto, atual


class ClassificationWarning(UserWarning):
    """A tabela lida não é de compartilhamento amplo: cuidado com o que sai do órgão."""


class AccessDenied(PermissionError):
    """O papel informado não tem autorização vigente para ler o produto interno."""


class CutNotAvailable(LookupError):
    """O motor não guarda a tabela nessa data de corte."""


@dataclass
class Description:
    table: str
    urn: str
    unit: str
    layer: str
    purpose: str
    usage: str
    limitations: str
    classification: str
    columns: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        linhas = [
            f"{self.table}  ({self.urn})",
            f"  unidade: {self.unit} · camada: {self.layer} · classificação: {self.classification}",
            f"  propósito: {self.purpose}",
            f"  uso: {self.usage}",
            f"  limitações: {self.limitations}",
            "  colunas:",
        ]
        linhas += [
            f"    - {c['name']:<28} {c['type']:<10} {c['sharing']:<11}{' pessoal' if c['personal'] else ''}"
            f"  {c['description']}"
            for c in self.columns
        ]
        return "\n".join(linhas)


@dataclass
class PublishedEdition:
    produto: str
    competencia: str
    datapackage: dict
    pasta: Path
    coluna_periodo: str = "period"

    @property
    def provenance(self) -> dict:
        x = self.datapackage["x-statera"]
        return {
            "product": x["product"],
            "period": x["period"],
            "cut_date": x["cutDate"],
            "version": self.datapackage["version"],
            "run": x.get("run"),
            "code_version": x.get("codeVersion"),
            "corrects": (x.get("corrects") or {}).get("version"),
            # corte de cada produto interno lido: {produto: execução que liberou o corte}
            "cuts": {nome: c["run"] for nome, c in (x.get("cuts") or {}).items()},
        }

    def ports(self) -> list[str]:
        return [r["name"] for r in self.datapackage["resources"] if r["format"] == "csv"]

    def table(self, port: str) -> ibis.Table:
        """O arquivo exatamente como foi publicado; o hash do Data Package é conferido."""
        porta = port
        recurso = next((r for r in self.datapackage["resources"] if r["name"] == porta), None)
        if recurso is None:
            raise KeyError(f"porta '{porta}' não está na edição; disponíveis: {self.ports()}")
        arquivo = self.pasta / recurso["path"]
        obtido = "sha256:" + hashlib.sha256(arquivo.read_bytes()).hexdigest()
        if obtido != recurso["hash"]:
            raise ValueError(
                f"{arquivo}: hash diverge do registrado ({recurso['hash']}); arquivo alterado após a publicação"
            )
        return ibis.memtable(pd.read_csv(arquivo, dtype={self.coluna_periodo: str}))


class Catalog:
    def __init__(self, projeto: Projeto, papel: str | None = None):
        self.projeto = projeto
        self.papel = papel
        self._contratos = {contratos.tabela_fisica(c): (caminho, c) for caminho, c in contratos.todos(projeto).values()}
        self._con = None

    @property
    def _coluna_corte(self) -> str:
        return self.projeto.convencoes["cut_date_column"]

    @property
    def con(self):
        if self._con is None:
            self._con = ibis.duckdb.connect(str(self.projeto.warehouse), read_only=True)
        return self._con

    # --- descoberta ---------------------------------------------------------------------------
    def search(
        self, theme: str | None = None, layer: str | None = None, product: str | None = None, text: str | None = None
    ) -> pd.DataFrame:
        """Tabelas do catálogo, filtradas por tema, camada, produto ou texto (no nome e no propósito)."""
        tema, camada, produto, texto = theme, layer, product, text
        temas = {p.nome: (p.ler("1-specify/product.odps.yaml") or {}) for p in self.projeto.produtos()}
        temas = {
            n: {c["property"]: c["value"] for c in o.get("customProperties", [])}.get("theme") for n, o in temas.items()
        }
        linhas = []
        for tabela, (caminho, c) in sorted(self._contratos.items()):
            unidade = self.projeto.unidade_de(caminho)
            linha = {
                "table": tabela,
                "layer": contratos.camada(c),
                "unit": unidade.nome if unidade else "",
                "theme": temas.get(unidade.nome) if unidade else None,
                "classification": classificacao.resumo(classificacao.da_tabela(c)),
                "proposito": " ".join(c["description"]["purpose"].split()),
            }
            if camada and linha["layer"] != camada:
                continue
            if produto and linha["unit"] != produto:
                continue
            if tema and linha["theme"] != tema:
                continue
            if texto and texto.lower() not in (tabela + linha["proposito"]).lower():
                continue
            linhas.append(linha)
        return pd.DataFrame(linhas)

    def _contrato(self, tabela: str) -> dict:
        if tabela not in self._contratos:
            raise KeyError(f"'{tabela}' não tem contrato no catálogo; tabelas: {sorted(self._contratos)}")
        return self._contratos[tabela][1]

    def describe(self, name: str) -> Description:
        tabela = name
        c = self._contrato(tabela)
        unidade = self.projeto.unidade_de(self._contratos[tabela][0])
        colunas = [
            {
                "name": p["name"],
                "type": p.get("logicalType", ""),
                "sharing": p.get("classification", ""),
                "personal": bool(classificacao.coluna_pessoal(p)),
                "description": p.get("description", ""),
            }
            for p in c["schema"][0]["properties"]
        ]
        return Description(
            table=tabela,
            urn=contratos.propriedade_custom(c, "urn") or "",
            unit=unidade.nome if unidade else "",
            layer=contratos.camada(c) or "",
            purpose=" ".join(c["description"]["purpose"].split()),
            usage=" ".join(c["description"].get("usage", "").split()),
            limitations=" ".join(c["description"].get("limitations", "").split()),
            classification=classificacao.resumo(classificacao.da_tabela(c)),
            columns=colunas,
        )

    # --- leitura --------------------------------------------------------------------------------
    def _avisar(self, tabela: str, c: dict) -> None:
        dims = classificacao.da_tabela(c)
        if dims.get("sharing") != "broad" or dims.get("nature") in ("personal", "sensitive-personal"):
            warnings.warn(
                f"{tabela} é {classificacao.resumo(dims)}: uso interno, sob as regras do contrato; "
                "agregue e aplique o controle de divulgação antes de compartilhar qualquer resultado.",
                ClassificationWarning,
                stacklevel=3,
            )

    def _autorizar(self, tabela: str) -> None:
        """Com um papel (quem não é da equipe dona), ler um produto interno exige autorização vigente."""
        if not self.papel:
            return
        from statera import portas

        dono = self.projeto.unidade_de(self._contratos[tabela][0])
        if dono is None or not dono.interno:
            return
        if not portas.autorizacao_vigente(dono, self.papel):
            raise AccessDenied(
                f"o papel '{self.papel}' não tem autorização vigente para ler {tabela} (produto interno "
                f"{dono.nome}). Use uma consulta catalogada ou peça a autorização, com finalidade e prazo, "
                "em authorizedConsumers do produto."
            )

    def cuts(self, name: str) -> list[str]:
        """Cortes LIBERADOS da tabela (manifestos do produto interno dono), do mais antigo ao mais novo."""
        tabela = name
        from statera.collect import cortes_liberados

        dono = self.projeto.unidade_de(self._contratos[tabela][0])
        return cortes_liberados(dono) if dono and dono.interno else []

    def table(self, name: str, cut_date: str | None = None) -> ibis.Table:
        """Tabela Ibis. Sem cut_date: o dado vigente (numa tabela por corte, o último corte LIBERADO).
        Com cut_date: o bronze reconstruído naquele corte, ou a partição daquele corte."""
        tabela, data_corte = name, cut_date
        c = self._contrato(tabela)
        self._autorizar(tabela)
        self._avisar(tabela, c)
        esquema, nome = tabela.split(".")
        if data_corte and contratos.camada(c) == "bronze":
            return self._bronze(tabela, c, nome, data_corte)
        t = self.con.table(nome, database=esquema)
        particao = contratos.propriedade_custom(c, "partitionedBy")
        if particao and not data_corte:
            liberados = self.cuts(tabela)
            if not liberados:
                raise CutNotAvailable(f"{tabela}: nenhum corte liberado ainda")
            data_corte = liberados[-1]
        if data_corte:
            if self._coluna_corte not in t.columns:
                raise CutNotAvailable(f"{tabela} não registra data de corte")
            liberados = self.cuts(tabela)
            if particao and liberados and data_corte not in liberados:
                # partição calculada mas não liberada (ex.: barrada na validação): não se lê
                raise CutNotAvailable(f"{tabela}: corte {data_corte} não foi liberado (liberados: {liberados})")
            filtrada = t.filter(t[self._coluna_corte] == ibis.literal(data_corte).cast("date"))
            if filtrada.count().execute() == 0:
                disponiveis = self.cuts(tabela) or _datas_de_corte(t, self._coluna_corte)
                raise CutNotAvailable(
                    f"{tabela} no corte {data_corte} não está disponível (cortes: {disponiveis}). Tabelas de "
                    "divulgação guardam só o último processamento; para o número publicado, use cat.edition(...)."
                )
            return filtrada
        return t

    def _bronze(self, tabela: str, contrato: dict, nome: str, data_corte: str) -> ibis.Table:
        """Bronze no corte: lotes até o do manifesto e recepção até a data. Sem manifesto, reconstrói
        pela data de recepção (com aviso: um lote atrasado poderia mudar o resultado)."""
        from statera.collect import extracao, ler_corte

        fonte = next(f for f in extracao.fontes(self.projeto) if f.tabela == nome)
        manifesto = ler_corte(fonte.produto, data_corte)
        lote_max = ((manifesto or {}).get("sources") or {}).get(nome, {}).get("batch")
        if lote_max is None:
            warnings.warn(f"{tabela}: corte {data_corte} não foi marcado; reconstruído só pela data de recepção",
                          ClassificationWarning, stacklevel=3)  # fmt: skip
        arquivos = [
            str(fonte.arquivo_lote(m["batch"])) for m in fonte.lotes() if lote_max is None or m["batch"] <= lote_max
        ]
        if not arquivos:
            raise CutNotAvailable(f"{tabela}: nenhum lote extraído até {data_corte}")
        t = self.con.read_parquet(arquivos, table_name=f"{nome}_{data_corte.replace('-', '')}", hive_partitioning=False)
        return t.filter(t[fonte.coluna_recepcao].cast("date") <= ibis.literal(data_corte).cast("date"))

    def provenance(self, name: str) -> dict:
        tabela = name
        c = self._contrato(tabela)
        t = self.con.table(tabela.split(".")[1], database=tabela.split(".")[0])
        col = self._coluna_corte
        cortes = self.cuts(tabela) or (_datas_de_corte(t, col) if col in t.columns else [])
        return {
            "table": tabela,
            "urn": contratos.propriedade_custom(c, "urn"),
            "contract": c["id"],
            "contract_version": c["version"],
            "cut_dates": cortes,
            "engine": self.projeto.namespace_dados,
        }

    def edition(self, product: str, period: str) -> PublishedEdition:
        produto, competencia = product, period
        unidade = self.projeto.produto(produto)
        caminho = registro.caminho_edicao(unidade, competencia)
        if not caminho.exists():
            raise LookupError(f"{produto} {competencia} não foi publicada (sem registro em {caminho})")
        return PublishedEdition(
            produto,
            competencia,
            json.loads(caminho.read_text(encoding="utf-8")),
            unidade.dados(7) / competencia,
            self.projeto.convencoes["period_column"],
        )

    def editions(self, product: str) -> pd.DataFrame:
        caminho = registro.caminho_indice(self.projeto.produto(product))
        return pd.read_csv(caminho, dtype=str) if caminho.exists() else pd.DataFrame()

    # --- métricas, consultas e SQL ---------------------------------------------------------------
    def metrics(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"metrica": n, "label": m["label"], "type": m["type"], "description": m["description"]}
                for n, m in metrics.definitions(self.projeto).items()
            ]
        )

    def metric(
        self,
        name: str,
        by: list[str] | None = None,
        period: str | None = None,
        periods: tuple[str, str] | None = None,
        filters: dict | None = None,
    ) -> pd.DataFrame:
        """Métrica oficial (metrics.yaml): agrupada por `by`, num período ou num intervalo (`periods`)."""
        return metrics.compute(name, by, period, periods, filters, self.projeto)

    def queries(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "id": c.id,
                    "title": c.title,
                    **{e: c.meta.get(e) for e in ("theme", "product", "frequency", "purpose", "status")},
                }
                for c in queries.load(self.projeto)
            ]
        )

    def query(self, query_id: str, **params) -> pd.DataFrame:
        """Executa uma consulta catalogada; os parâmetros são os declarados no query.yaml dela."""
        return queries.run(query_id, params, projeto=self.projeto)

    def sql(self, texto: str) -> pd.DataFrame:
        """SQL direto no motor (somente leitura)."""
        return self.con.sql(texto).to_pandas()

    def close(self) -> None:
        if self._con is not None:
            self._con.disconnect()
            self._con = None


def _datas_de_corte(t: ibis.Table, coluna: str) -> list[str]:
    return sorted(t.select(d=t[coluna].cast("string")).distinct().to_pandas()["d"].tolist())


def open(project: Projeto | None = None, role: str | None = None) -> Catalog:
    """Abre o catálogo. `role` (ou STATERA_PAPEL) identifica quem não é da equipe dona dos produtos
    internos: a leitura deles passa a exigir autorização vigente. Em produção, quem lê é identificado
    pelo login no motor, e as permissões do motor (grants gerados) são a fronteira de verdade."""
    import os

    return Catalog(project or atual(), role or os.environ.get("STATERA_PAPEL") or None)
