"""Como o público acessa cada produto de divulgação: distribuições e visualizações.

O produto de dados é o DADO (portas de saída com contrato). O que o público abre são:

- **distribuições** (arquivos), no vocabulário do DCAT (`dcat:Distribution`):
    * produto com série: derivadas de `7-disseminate/series.yaml` e do registro de edições (planilha,
      tabelas, metadados da última edição publicada), com a página e o conjunto no dados.gov.br;
    * produto ainda produzido fora do processo (nível 1): declaradas no descritor ODPS, em
      `customProperties.distributions`;
- **visualizações**: consumidores declarados como `exposures` do dbt, com `meta.visualization`:
    * `painel`: painel de BI (Power BI, Superset, Metabase…);
    * `grafico`: gráficos estáticos (PNG, SVG) publicados no portal.

Uma visualização pública (`compartilhamento: amplo`) lê a EDIÇÃO PUBLICADA, para mostrar o mesmo
número da planilha. Um gráfico estático declara, para cada imagem, o texto alternativo e a porta de
saída com os números do gráfico em tabela (e-MAG; Lei 13.146/2015, art. 63; WCAG 1.1.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from statera import contratos, portas
from statera.projeto import Projeto, Unidade

LEITURAS = ("published-edition", "cut-date", "current")
VISUALIZACOES = ("dashboard", "chart")
CAMPOS_DISTRIBUICAO = ("title", "format", "url", "updated")
CAMPOS_VISUALIZACAO = ("product", "tool", "reads", "updated", "sharing")


@dataclass
class Distribuicao:
    title: str
    formato: str
    url: str | None = None
    atualizacao: str = ""
    origem: str = "declarada"  # "series" (gerada pelo processo) ou "declarada" (ODPS)
    edicao: str | None = None  # competência da última edição publicada
    registro: Path | None = None  # Data Package da última edição, no registro
    portas: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Distribuições
# ---------------------------------------------------------------------------


def declaradas(unidade: Unidade) -> list[dict]:
    return contratos.propriedade_custom(unidade.odps, "distributions", []) or []


def distribuicoes(unidade: Unidade) -> list[Distribuicao]:
    from statera.disseminate import registro

    saida = []
    if unidade.tem("7-disseminate/series.yaml"):
        s = registro.serie(unidade)
        arq, pagina = s["files_per_edition"], s.get("page")
        edicoes = registro.edicoes(unidade)
        ultima = edicoes[-1]["x-statera"]["period"] if edicoes else None
        caminho = registro.caminho_edicao(unidade, ultima) if ultima else None
        nomes_portas = tuple(p["name"] for p in portas.portas_de_saida(unidade))
        planilha = arq["spreadsheet"].format(competencia=ultima or "AAAA-MM")
        saida += [
            Distribuicao(f"Planilha ({planilha})", "xlsx", pagina, s["frequency"], "series", ultima, caminho,
                         nomes_portas),
            Distribuicao(f"Tabelas ({len(nomes_portas)} arquivos CSV)", "csv", pagina, s["frequency"], "series",
                         ultima, caminho, nomes_portas),
        ]  # fmt: skip
        if gov := s.get("open_data_portal"):
            titulo = f"Dados abertos: dados.gov.br, conjunto `{gov['dataset']}` ({gov['organization']})"
            saida.append(Distribuicao(titulo, "CKAN", None, s["frequency"], "series", ultima, caminho,
                                      nomes_portas))  # fmt: skip
    for d in declaradas(unidade):
        saida.append(Distribuicao(d.get("title", ""), d.get("format", ""), d.get("url"), d.get("updated", ""),
                                  "declarada", portas=tuple(d.get("ports", []))))  # fmt: skip
    return saida


def problemas_distribuicoes(unidade: Unidade) -> list[str]:

    problemas = []
    nomes_portas = {p["name"] for p in portas.portas_de_saida(unidade)}
    for i, d in enumerate(declaradas(unidade), 1):
        rotulo = d.get("title") or f"distribuição {i}"
        problemas += [f"{rotulo}: falta '{c}'" for c in CAMPOS_DISTRIBUICAO if not d.get(c)]
        problemas += [f"{rotulo}: porta '{p}' não existe" for p in d.get("ports", []) if p not in nomes_portas]
    if not unidade.interno and not unidade.tem("7-disseminate/series.yaml") and not declaradas(unidade):
        problemas.append("produto de divulgação sem forma de acesso: series.yaml ou customProperties.distributions")
    return problemas


# ---------------------------------------------------------------------------
# Visualizações (exposures)
# ---------------------------------------------------------------------------


def visualizacoes(manifesto: dict) -> list[dict]:
    return sorted(
        (e for e in manifesto["exposures"].values() if (e.get("meta") or {}).get("visualization")),
        key=lambda e: e["name"],
    )


def problemas_visualizacao(projeto: Projeto, e: dict) -> list[str]:
    meta, nome = e.get("meta") or {}, e["name"]
    tipo = meta.get("visualization")
    if tipo not in VISUALIZACOES:
        return [f"{nome}: meta.visualization '{tipo}' deveria ser um de {list(VISUALIZACOES)}"]
    problemas = [f"{nome}: falta meta.{c}" for c in CAMPOS_VISUALIZACAO if not meta.get(c)]
    if not e.get("url"):
        problemas.append(f"{nome}: falta url (onde o público abre a visualização)")
    if meta.get("reads") and meta["reads"] not in LEITURAS:
        problemas.append(f"{nome}: meta.reads '{meta['reads']}' deveria ser um de {list(LEITURAS)}")
    if meta.get("sharing") == "broad" and meta.get("reads") != "published-edition":
        problemas.append(
            f"{nome}: visualização pública deve ler a edição publicada (meta.reads: published-edition), "
            "para mostrar o mesmo número da planilha"
        )
    unidade = next((u for u in projeto.produtos() if u.nome == meta.get("product")), None)
    if meta.get("product") and unidade is None:
        problemas.append(f"{nome}: produto '{meta['product']}' não existe")
    problemas += [f"{nome}: autor sem nome em meta.authors" for a in meta.get("authors") or [] if not a.get("name")]
    if tipo == "chart":
        graficos = meta.get("charts") or []
        if not graficos:
            problemas.append(f"{nome}: gráfico sem meta.charts (título, porta e texto alternativo de cada imagem)")
        nomes_portas = {p["name"] for p in portas.portas_de_saida(unidade)} if unidade else set()
        for i, g in enumerate(graficos, 1):
            rotulo = g.get("title") or f"gráfico {i}"
            if not g.get("alt_text"):
                problemas.append(f"{nome} / {rotulo}: falta alt_text (e-MAG, WCAG 1.1.1)")
            if g.get("port") not in nomes_portas:
                problemas.append(f"{nome} / {rotulo}: porta '{g.get('port')}' não é saída do produto")
    return problemas
