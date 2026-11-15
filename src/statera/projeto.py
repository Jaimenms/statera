"""O repositório de conteúdo: `statera.toml` e as unidades que ele contém.

Estrutura (especificação v0, `specification/01-repositorio-de-conteudo.md`):

    statera.toml              identidade do órgão, espaço de nomes, motor, registro de edições
    governance/                papéis, responsáveis transversais, ADRs, processos transversais
    references/<nome>/        referência oficial: CSV + contrato ODCS com autoridade e temporalidade
    products/<nome>/           produtos de dados com as fases do GSBPM que lhes cabem. Pelo alinhamento:
                                 fonte     interno, extração incremental de um sistema (bronze)
                                 agregado  interno, entidades canônicas por corte (silver)
                                 consumo   publicado, tabelas de divulgação (gold)
    queries/                 consultas recorrentes que atravessam produtos
    recipes/                  exemplos executáveis, testados
    dbt_project.yml            projeto dbt do órgão, na raiz (GERADO), com packages.yml e profiles.yml
    dbt/                       infraestrutura do dbt: macros/, generated/, dbt_packages/, data/ (motor)
    docs/                      documentação GERADA

Uma UNIDADE é um produto ou uma referência. Cada unidade tem dono, contratos e, nas fases que lhe
cabem, uma pasta `data/` (fora do git) com o que cada execução produz. O domínio (área dona) é o
atributo `domain` do descritor ODPS, não uma pasta (ADR 0016).

Variáveis de ambiente:
    STATERA_PROJETO   raiz do repositório de conteúdo (padrão: procura statera.toml a partir do cwd)
    STATERA_DADOS     troca a base de TODAS as pastas data/ (útil em testes)
    STATERA_REGISTRO  troca o local do registro de edições
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from dataclasses import dataclass, field
from functools import cache, cached_property
from pathlib import Path

import yaml

PACOTE = Path(__file__).resolve().parent
ESQUEMAS = PACOTE / "schemas"
ESQUEMAS_PADROES = ESQUEMAS / "standards"
ESQUEMAS_STATERA = ESQUEMAS / "statera"
MACROS_DBT = PACOTE / "dbt"

FASES = {
    1: "1-specify",
    2: "2-design",
    3: "3-build",
    4: "4-collect",
    5: "5-process",
    6: "6-analyse",
    7: "7-disseminate",
    8: "8-evaluate",
}
PASTAS_TIPO = {"product": "products", "reference": "references"}
DESCRITORES = {"product": "1-specify/product.odps.yaml"}
ALINHAMENTOS = ("source", "aggregate", "consumer")
VISIBILIDADES = ("internal", "public")


class ErroProjeto(RuntimeError):
    """Repositório de conteúdo ausente ou mal configurado."""


def ler_yaml(caminho: Path):
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


# Esqueleto do produto (especificação 01): todo produto tem estes arquivos, com conteúdo ou com
# `not_applicable: <motivo>`. Assim a estrutura é a mesma em todos, e um arquivo que não se aplica
# diz por quê, em vez de faltar (ausência é ambígua: não se aplica ou esqueceram?).
ESQUELETO = {
    "1-specify/calendar.yaml": "calendar",
    "2-design/processes/to-be.yaml": "process",
    "6-analyse/controls.yaml": "controls",
    "7-disseminate/series.yaml": "series",
    "8-evaluate/indicators.yaml": "indicators",
}


def nao_se_aplica(caminho: Path) -> str | None:
    """Motivo declarado num arquivo que não se aplica ao produto (`not_applicable: <motivo>`);
    None se o arquivo não existe ou tem conteúdo."""
    if not caminho.exists():
        return None
    dados = ler_yaml(caminho)
    if isinstance(dados, dict) and set(dados) == {"not_applicable"}:
        return str(dados["not_applicable"] or "")
    return None


@dataclass(frozen=True)
class Unidade:
    """Produto (de divulgação ou interno) ou referência."""

    tipo: str
    nome: str
    pasta: Path
    projeto: Projeto = field(compare=False, repr=False)

    def fase(self, numero: int) -> Path:
        return self.pasta / FASES[numero]

    def dados(self, numero: int) -> Path:
        """Pasta data/ da fase (fora do git). Com STATERA_DADOS, fica sob essa base."""
        base = os.environ.get("STATERA_DADOS")
        if base:
            return Path(base).resolve() / PASTAS_TIPO[self.tipo] / self.nome / FASES[numero]
        return self.fase(numero) / "data"

    @property
    def relativo(self) -> str:
        return self.pasta.relative_to(self.projeto.raiz).as_posix()

    @property
    def urn(self) -> str:
        return f"{self.projeto.espaco_de_nomes}.{self.nome}"

    @property
    def descritor(self) -> Path | None:
        return self.pasta / DESCRITORES[self.tipo] if self.tipo in DESCRITORES else None

    @property
    def odps(self) -> dict:
        return self.ler(DESCRITORES["product"]) or {} if self.tipo == "product" else {}

    def propriedade(self, nome: str, padrao=None):
        for item in self.odps.get("customProperties", []) or []:
            if item.get("property") == nome:
                return item.get("value")
        return padrao

    @property
    def alinhamento(self) -> str:
        """fonte | agregado | consumo (padrão: consumo)."""
        return self.propriedade("alignment", "consumer") if self.tipo == "product" else "reference"

    @property
    def interno(self) -> bool:
        return self.tipo == "product" and self.propriedade("visibility", "public") == "internal"

    def ler(self, relativo: str):
        """Lê um YAML da unidade (ex.: '1-specify/calendar.yaml'); None se não existir ou se
        declarar `not_applicable`."""
        caminho = self.pasta / relativo
        return ler_yaml(caminho) if self.tem(relativo) else None

    def tem(self, relativo: str) -> bool:
        """O arquivo existe e tem conteúdo (não declara `not_applicable`)."""
        caminho = self.pasta / relativo
        return caminho.exists() and nao_se_aplica(caminho) is None

    def nao_se_aplica(self, relativo: str) -> str | None:
        return nao_se_aplica(self.pasta / relativo)

    def contratos(self) -> list[Path]:
        return sorted(c for c in self.pasta.rglob("*.odcs.yaml") if "data" not in c.parts)

    def processos(self) -> list[Path]:
        return sorted(c for c in (self.fase(2) / "processes").glob("*.yaml") if nao_se_aplica(c) is None)

    @property
    def processo_to_be(self) -> Path:
        return self.fase(2) / "processes" / "to-be.yaml"

    @property
    def pasta_execucoes(self) -> Path:
        """Registro das execuções do processo (duração, estado, OpenLineage): insumo da fase 8."""
        return self.dados(8) / "execucoes"


class Projeto:
    def __init__(self, raiz: Path):
        self.raiz = raiz.resolve()
        arquivo = self.raiz / "statera.toml"
        if not arquivo.exists():
            raise ErroProjeto(f"{arquivo} não encontrado")
        self.config = tomllib.loads(arquivo.read_text(encoding="utf-8"))

    def __repr__(self) -> str:
        return f"Projeto({self.raiz})"

    # --- identidade ------------------------------------------------------------------------
    def _secao(self, nome: str) -> dict:
        return self.config.get(nome, {})

    @property
    def nome(self) -> str:
        return self._secao("project").get("name", self.raiz.name)

    @property
    def orgao(self) -> str:
        return self._secao("project").get("organization", "")

    @property
    def sigla(self) -> str:
        return self._secao("project")["acronym"]

    @property
    def espaco_de_nomes(self) -> str:
        return self._secao("project")["namespace"]

    @property
    def convencoes(self) -> dict:
        """Colunas de convenção dos dados ([conventions] do statera.toml): a do período de referência e a
        da data de corte. Os nomes das colunas são do conteúdo (no idioma dele); o padrão é em inglês."""
        c = self._secao("conventions")
        return {
            "period_column": c.get("period_column", "period"),
            "cut_date_column": c.get("cut_date_column", "cut_date"),
            "valid_from_column": c.get("valid_from_column", "valid_from"),
            "valid_to_column": c.get("valid_to_column", "valid_to"),
        }

    @property
    def sintetico(self) -> bool:
        return bool(self._secao("project").get("synthetic", False))

    # --- unidades --------------------------------------------------------------------------
    @cached_property
    def unidades(self) -> list[Unidade]:
        encontradas = []
        for tipo, pasta in PASTAS_TIPO.items():
            base = self.raiz / pasta
            if not base.is_dir():
                continue
            for sub in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
                if tipo in DESCRITORES and not (sub / DESCRITORES[tipo]).exists():
                    continue
                if tipo == "reference" and not any(sub.glob("*.odcs.yaml")):
                    continue
                encontradas.append(Unidade(tipo, sub.name, sub, self))
        return encontradas

    def _do_tipo(self, tipo: str) -> list[Unidade]:
        return [u for u in self.unidades if u.tipo == tipo]

    def produtos(self) -> list[Unidade]:
        return self._do_tipo("product")

    def referencias(self) -> list[Unidade]:
        return self._do_tipo("reference")

    def unidade(self, nome: str, tipo: str | None = None) -> Unidade:
        for u in self.unidades:
            if u.nome == nome and (tipo is None or u.tipo == tipo):
                return u
        raise ErroProjeto(f"Unidade '{nome}' ({tipo or 'qualquer tipo'}) não encontrada em {self.raiz}")

    def produto(self, nome: str) -> Unidade:
        return self.unidade(nome, "product")

    def unidade_de(self, caminho: Path) -> Unidade | None:
        caminho = caminho.resolve()
        for u in self.unidades:
            if caminho == u.pasta or u.pasta in caminho.parents:
                return u
        return None

    # --- pastas transversais ---------------------------------------------------------------
    @property
    def governanca(self) -> Path:
        return self.raiz / "governance"

    @property
    def docs(self) -> Path:
        return self.raiz / "docs"

    @property
    def receitas(self) -> Path:
        return self.raiz / "recipes"

    def pastas_consultas(self) -> list[Path]:
        return [self.raiz / "queries", *(p.pasta / "queries" for p in self.produtos())]

    def processos(self) -> list[Path]:
        transversais = sorted((self.governanca / "processes").glob("*.yaml"))
        return [*(c for u in self.unidades for c in u.processos()), *transversais]

    def pastas_regras(self) -> list[Path]:
        return [u.fase(5) / "rules" for u in self.unidades if (u.fase(5) / "rules").is_dir()]

    def responsaveis(self) -> list[Path]:
        """shared-owners.yaml e o owners.yaml de cada unidade com fase 1."""
        unidades = [u.fase(1) / "owners.yaml" for u in self.unidades]
        return [self.governanca / "shared-owners.yaml", *(c for c in unidades if c.exists())]

    # --- motor e dbt ------------------------------------------------------------------------
    @property
    def motor(self) -> dict:
        return {"type": "duckdb", "path": "dbt/data/warehouse.duckdb", **self._secao("engine")}

    @property
    def warehouse(self) -> Path:
        base = os.environ.get("STATERA_DADOS")
        nome = Path(self.motor["path"]).name
        return Path(base).resolve() / "engine" / nome if base else self.raiz / self.motor["path"]

    @property
    def dbt_projeto(self) -> Path:
        """Pasta do dbt_project.yml, packages.yml e profiles.yml: a raiz do repositório.

        Os modelos, seeds e testes moram nas unidades; com o projeto na raiz, todos ficam DENTRO do
        projeto dbt, como o guia do dbt e as ferramentas (dbt Power User) esperam (ADR 0017).
        """
        return self.raiz

    @property
    def dbt_pasta(self) -> Path:
        """Infraestrutura do dbt que não é das unidades: macros/, generated/, dbt_packages/."""
        return self.raiz / self._secao("dbt").get("folder", "dbt")

    @property
    def dbt_nome(self) -> str:
        return self._secao("dbt").get("project", self.nome.replace("-", "_"))

    @property
    def dbt_perfil(self) -> str:
        return self._secao("dbt").get("profile", self.nome.replace("-", "_"))

    @property
    def dbt_manifesto(self) -> Path:
        return self.dbt_projeto / "target" / "manifest.json"

    @property
    def dbt_resultados(self) -> Path:
        return self.dbt_projeto / "target" / "run_results.json"

    # --- registro, linhagem ------------------------------------------------------------------
    @property
    def registro_edicoes(self) -> Path:
        padrao = self._secao("registry").get("editions", "../publications")
        return Path(os.environ.get("STATERA_REGISTRO") or self.raiz / padrao).resolve()

    @property
    def openlineage_namespace(self) -> str:
        return self._secao("openlineage").get("namespace", self.espaco_de_nomes)

    @property
    def namespace_dados(self) -> str:
        return f"{self.motor['type']}://{Path(self.motor['path']).name}"

    # --- utilidades -------------------------------------------------------------------------
    def rel(self, caminho: Path) -> str:
        """Caminho relativo à raiz (ou absoluto, se estiver fora dela, como em testes)."""
        try:
            return caminho.resolve().relative_to(self.raiz).as_posix()
        except ValueError:
            return str(caminho)

    def pastas_dados(self) -> list[Path]:
        pastas = [u.dados(n) for u in self.unidades for n in FASES if u.dados(n).exists()]
        return [*pastas, self.warehouse.parent]

    def gancho(self, especificacao: str):
        """Carrega uma função do conteúdo declarada como 'caminho/arquivo.py:funcao'."""
        arquivo, _, funcao = especificacao.partition(":")
        caminho = self.raiz / arquivo
        spec = importlib.util.spec_from_file_location(f"statera_gancho_{caminho.stem}", caminho)
        if spec is None or spec.loader is None:
            raise ErroProjeto(f"Gancho {especificacao} não pôde ser carregado")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = modulo  # dataclasses e anotações precisam do módulo registrado
        spec.loader.exec_module(modulo)
        return getattr(modulo, funcao)

    @property
    def demo(self) -> dict:
        return self._secao("demo")


def descobrir_raiz(inicio: Path | None = None) -> Path:
    if os.environ.get("STATERA_PROJETO"):
        return Path(os.environ["STATERA_PROJETO"]).resolve()
    atual_ = (inicio or Path.cwd()).resolve()
    for pasta in (atual_, *atual_.parents):
        if (pasta / "statera.toml").exists():
            return pasta
    raise ErroProjeto("statera.toml não encontrado no diretório atual nem acima (ou defina STATERA_PROJETO)")


@cache
def _carregar(raiz: Path, _dados: str | None, _registro: str | None) -> Projeto:
    return Projeto(raiz)


def atual() -> Projeto:
    """O projeto do diretório atual. Recarrega se as variáveis de ambiente mudarem (testes)."""
    return _carregar(descobrir_raiz(), os.environ.get("STATERA_DADOS"), os.environ.get("STATERA_REGISTRO"))
