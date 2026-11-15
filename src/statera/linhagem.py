"""Grafo de linhagem: fontes -> bronze -> silver -> gold -> consumidores.

Construído a partir do `manifest.json` do dbt (sources, seeds, models, exposures) e enriquecido
com os contratos ODCS (contrato e classificação de cada ativo). Os sistemas de origem vêm dos
contratos do bronze (`extracao.sistemaOrigem`); destinos externos, do `meta.external_destination` das
exposures; as consultas recorrentes (analyses do dbt) aparecem como consumidoras.
Saídas:
  - Mermaid (renderizado nativamente pelo GitHub e por extensões do VS Code);
  - SVG com layout em camadas, sem dependências externas (aparece em qualquer visualizador);
  - análise de impacto: tudo o que está a jusante de um nó.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import dataclass, field

from statera import classificacao, contratos
from statera.projeto import atual

ORDEM_CAMADAS = ["source", "bronze", "ref", "silver", "gold", "consumer"]
ROTULO_CAMADA = {
    "source": "Sistemas de origem",
    "bronze": "Bronze",
    "ref": "Referências (ref)",
    "silver": "Silver",
    "gold": "Gold",
    "consumer": "Consumidores",
}
CORES = {  # fundo, borda
    "source": ("#ECEFF4", "#4C566A"),
    "bronze": ("#F6E3D3", "#9C5B2E"),
    "ref": ("#E1ECF7", "#2F6497"),
    "silver": ("#EDEDED", "#6B6B6B"),
    "gold": ("#FBF0C9", "#9A7B0A"),
    "consumer": ("#EAE2F5", "#5E3C99"),
}


@dataclass
class No:
    id: str
    rotulo: str
    camada: str
    tipo: str
    contrato: str | None = None
    classificacao: str | None = None
    descricao: str = ""
    url: str | None = None  # visualizações e destinos com endereço público


@dataclass
class Grafo:
    nos: dict[str, No] = field(default_factory=dict)
    arestas: set[tuple[str, str]] = field(default_factory=set)

    def pais(self, no: str) -> list[str]:
        return sorted(a for a, b in self.arestas if b == no)

    def filhos(self, no: str) -> list[str]:
        return sorted(b for a, b in self.arestas if a == no)


# ---------------------------------------------------------------------------
# Construção
# ---------------------------------------------------------------------------


def _indice_contratos() -> tuple[dict[str, dict], dict[str, dict]]:
    por_modelo, por_source = {}, {}
    for _, c in contratos.todos().values():
        if m := contratos.propriedade_custom(c, "dbtModel") or contratos.propriedade_custom(c, "dbtSeed"):
            por_modelo[m] = c
        if s := contratos.propriedade_custom(c, "dbtSource"):
            por_source[s] = c
    return por_modelo, por_source


def _anotar(no: No, contrato: dict | None) -> No:
    if contrato:
        no.contrato = contrato["id"]
        dims = classificacao.da_tabela(contrato)
        no.classificacao = f"{dims.get('sharing', '?')} · {dims.get('nature', '?')}"
    return no


def _camada_modelo(no: dict) -> str:
    meta = (no.get("config", {}).get("meta") or {}) | (no.get("meta") or {})
    return meta.get("layer", "silver")


def _adicionar_sources(g: Grafo, manifesto: dict, por_source: dict) -> None:
    for uid, s in manifesto["sources"].items():
        chave = f"{s['source_name']}.{s['name']}"
        no = No(uid, f"bronze.{s['name']}", "bronze", "source", descricao=s.get("description", ""))
        g.nos[uid] = _anotar(no, por_source.get(chave))
        extracao = contratos.propriedade_custom(por_source.get(chave) or {}, "extraction") or {}
        nome_sistema = extracao.get("sourceSystem", s["source_name"])
        sistema = "sistema." + "".join(c if c.isalnum() else "_" for c in nome_sistema.lower())
        g.nos.setdefault(sistema, No(sistema, nome_sistema, "source", "system"))
        g.arestas.add((sistema, uid))


def _adicionar_nos_dbt(g: Grafo, manifesto: dict, por_modelo: dict) -> None:
    for uid, n in manifesto["nodes"].items():
        tipo = n["resource_type"]
        if tipo not in ("seed", "model"):
            continue
        camada = "ref" if tipo == "seed" else _camada_modelo(n)
        no = No(uid, n["name"], camada, tipo, descricao=n.get("description", ""))
        g.nos[uid] = _anotar(no, por_modelo.get(n["name"]))
    for uid, n in manifesto["nodes"].items():
        if n["resource_type"] == "model":
            g.arestas.update((pai, uid) for pai in n["depends_on"]["nodes"] if pai in g.nos)


def _adicionar_exposures(g: Grafo, manifesto: dict) -> None:
    for uid, e in manifesto["exposures"].items():
        meta = e.get("meta") or {}
        g.nos[uid] = No(
            uid,
            e.get("label") or e["name"],
            "consumer",
            f"exposure:{e['type']}",
            classificacao=meta.get("classification"),
            descricao=e.get("description", ""),
            url=e.get("url"),
        )
        g.arestas.update((pai, uid) for pai in e["depends_on"]["nodes"] if pai in g.nos)
        if destino := meta.get("external_destination"):
            externo = "externo." + "".join(c if c.isalnum() else "_" for c in destino.lower())
            g.nos[externo] = No(externo, destino, "consumer", "externo")
            g.arestas.add((uid, externo))


def _adicionar_consultas(g: Grafo, manifesto: dict) -> None:
    """Consultas recorrentes (analyses do dbt): consumidoras que aparecem na linhagem."""
    for uid, n in manifesto["nodes"].items():
        if n["resource_type"] != "analysis":
            continue
        g.nos[uid] = No(uid, f"consulta: {n['name']}", "consumer", "query", descricao=n.get("description", ""))
        g.arestas.update((pai, uid) for pai in n["depends_on"]["nodes"] if pai in g.nos)


def construir(manifesto: dict | None = None) -> Grafo:
    if manifesto is None:
        manifesto = json.loads(atual().dbt_manifesto.read_text())
    por_modelo, por_source = _indice_contratos()
    g = Grafo()
    _adicionar_sources(g, manifesto, por_source)
    _adicionar_nos_dbt(g, manifesto, por_modelo)
    _adicionar_exposures(g, manifesto)
    _adicionar_consultas(g, manifesto)
    return g


def localizar(g: Grafo, termo: str) -> str:
    if termo in g.nos:
        return termo
    candidatos = [
        uid for uid, n in g.nos.items() if n.rotulo == termo or uid.endswith(f".{termo}") or n.contrato == termo
    ]
    if not candidatos:
        raise KeyError(f"Nó '{termo}' não encontrado na linhagem")
    return candidatos[0]


def impacto(g: Grafo, termo: str) -> list[str]:
    """Todos os nós a jusante, ordenados por posto (do mais próximo ao mais distante)."""
    inicio = localizar(g, termo)
    vistos, pilha = set(), [inicio]
    while pilha:
        atual = pilha.pop()
        for filho in g.filhos(atual):
            if filho not in vistos:
                vistos.add(filho)
                pilha.append(filho)
    postos = _postos(g)
    return sorted(vistos, key=lambda n: (postos[n], g.nos[n].rotulo))


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def _mid(uid: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in uid)


def para_mermaid(g: Grafo) -> str:
    linhas = ["flowchart LR"]
    for camada in ORDEM_CAMADAS:
        nos = sorted((n for n in g.nos.values() if n.camada == camada), key=lambda n: n.rotulo)
        if not nos:
            continue
        linhas.append(f'  subgraph {camada}["{ROTULO_CAMADA[camada]}"]')
        for n in nos:
            extra = f"<br/><small>{n.classificacao}</small>" if n.classificacao else ""
            abre, fecha = ('[("', '")]') if n.tipo in ("source", "seed") else ('["', '"]')
            linhas.append(f"    {_mid(n.id)}{abre}{n.rotulo}{extra}{fecha}")
        linhas.append("  end")
    linhas += [f"  {_mid(a)} --> {_mid(b)}" for a, b in sorted(g.arestas)]
    linhas += [
        f'  click {_mid(n.id)} href "{n.url}" _blank' for n in sorted(g.nos.values(), key=lambda n: n.id) if n.url
    ]
    for camada, (fundo, borda) in CORES.items():
        linhas.append(f"  classDef {camada} fill:{fundo},stroke:{borda},color:#1F1F1F")
        ids = [_mid(n.id) for n in g.nos.values() if n.camada == camada]
        if ids:
            linhas.append(f"  class {','.join(ids)} {camada}")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# SVG com layout em camadas, de cima para baixo (Sugiyama simplificado)
# ---------------------------------------------------------------------------


def _postos(g: Grafo) -> dict[str, int]:
    posto: dict[str, int] = {}

    def calcular(n: str) -> int:
        if n not in posto:
            pais = g.pais(n)
            posto[n] = 0 if not pais else 1 + max(calcular(p) for p in pais)
        return posto[n]

    for n in g.nos:
        calcular(n)
    # Referências e outras raízes que não são sistemas de origem ficam junto de quem as usa
    for n, no in g.nos.items():
        if not g.pais(n) and no.camada != "source" and g.filhos(n):
            posto[n] = max(1, min(posto[f] for f in g.filhos(n)) - 1)
    # Consumidores sempre nas últimas linhas, abaixo de todo o gold
    base = 1 + max((p for n, p in posto.items() if g.nos[n].camada != "consumer"), default=0)
    for n, no in g.nos.items():
        if no.camada == "consumer":
            posto[n] = base + (1 if no.tipo == "externo" else 0)
    return posto


def _baricentro(g: Grafo, n: str, posicao: dict[str, int]) -> float:
    vizinhos = g.pais(n) + g.filhos(n)
    return sum(posicao[v] for v in vizinhos) / len(vizinhos) if vizinhos else posicao[n]


def _ordenar(g: Grafo, postos: dict[str, int]) -> dict[int, list[str]]:
    linhas: dict[int, list[str]] = defaultdict(list)
    chave_inicial = {n: (ORDEM_CAMADAS.index(g.nos[n].camada), g.nos[n].rotulo) for n in g.nos}
    for n in sorted(g.nos, key=chave_inicial.get):
        linhas[postos[n]].append(n)
    for _ in range(6):  # varreduras de baricentro, para reduzir cruzamentos
        for alvo in sorted(linhas):
            posicao = {n: i for linha in linhas.values() for i, n in enumerate(linha)}
            pesos = {n: _baricentro(g, n, posicao) for n in linhas[alvo]}
            linhas[alvo].sort(key=pesos.get)
    return linhas


def _desenhar_no(no: No, x: float, y: float, larg: float, alt: float) -> list[str]:
    fundo, borda = CORES[no.camada]
    cilindro = no.tipo in ("source", "seed")
    if cilindro:
        forma = (
            f'<path d="M{x},{y + 6} v{alt - 12} a{larg / 2},6 0 0 0 {larg},0 v-{alt - 12} '
            f'a{larg / 2},6 0 0 0 -{larg},0 a{larg / 2},6 0 0 0 {larg},0" fill="{fundo}" '
            f'stroke="{borda}" stroke-width="1.4"/>'
        )
    else:
        forma = (
            f'<rect x="{x}" y="{y}" width="{larg}" height="{alt}" rx="7" fill="{fundo}" '
            f'stroke="{borda}" stroke-width="1.4"/>'
        )
    deslocamento = 5 if cilindro else 0
    rotulo = no.rotulo if len(no.rotulo) <= 34 else no.rotulo[:32] + "…"
    partes = [
        forma,
        f'<text x="{x + larg / 2}" y="{y + 20 + deslocamento}" font-size="11.5" '
        f'font-weight="bold" text-anchor="middle" fill="#1F1F1F">{html.escape(rotulo)}</text>',
    ]
    sub = " · ".join(filter(None, [no.classificacao, "ODCS" if no.contrato else None]))
    if sub:
        partes.append(
            f'<text x="{x + larg / 2}" y="{y + 35 + deslocamento}" font-size="10" '
            f'text-anchor="middle" fill="{borda}">{html.escape(sub)}</text>'
        )
    if no.url:
        partes = [f'<a href="{html.escape(no.url, quote=True)}" target="_blank">', *partes, "</a>"]
    return partes


def para_svg(g: Grafo, titulo: str = "Linhagem de dados") -> str:
    postos = _postos(g)
    linhas = _ordenar(g, postos)
    larg, alt, dx, dy, topo = 215, 48, 240, 92, 130
    max_por_linha = max(len(nos) for nos in linhas.values())
    largura = max(80 + max_por_linha * dx, 1080)
    altura = topo + (max(linhas) + 1) * dy + 20
    pos: dict[str, tuple[float, float]] = {}
    for indice, nos in linhas.items():
        inicio = (largura - len(nos) * dx) / 2 + (dx - larg) / 2
        for i, n in enumerate(nos):
            pos[n] = (inicio + i * dx, topo + indice * dy)

    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura:.0f} {altura:.0f}" '
        f'width="{largura:.0f}" height="{altura:.0f}" font-family="Helvetica, Arial, sans-serif" '
        f'role="img" aria-label="{html.escape(titulo)}">',
        '<defs><marker id="s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#8A8A8A"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="30" y="38" font-size="18" font-weight="bold" fill="#1F1F1F">{html.escape(titulo)}</text>',
    ]
    for i, camada in enumerate(ORDEM_CAMADAS):
        fundo, borda = CORES[camada]
        x = 30 + i * 170
        o.append(
            f'<rect x="{x}" y="58" width="22" height="14" rx="3" fill="{fundo}" stroke="{borda}"/>'
            f'<text x="{x + 30}" y="69" font-size="12" fill="#333">{ROTULO_CAMADA[camada]}</text>'
        )
    o.append(
        '<text x="30" y="96" font-size="11" fill="#555">Cilindro = tabela armazenada (bronze, ref). '
        "Caixa = modelo ou consumidor. Abaixo do nome: compartilhamento · natureza "
        "(Decreto 10.046 e LGPD) e se há contrato ODCS.</text>"
    )
    for a, b in sorted(g.arestas):
        (x1, y1), (x2, y2) = pos[a], pos[b]
        x1, x2, y1 = x1 + larg / 2, x2 + larg / 2, y1 + alt
        c = (y2 - y1) / 2
        o.append(
            f'<path d="M{x1:.1f},{y1:.1f} C{x1:.1f},{y1 + c:.1f} {x2:.1f},{y2 - c:.1f} '
            f'{x2:.1f},{y2 - 2:.1f}" fill="none" stroke="#9A9A9A" stroke-width="1.3" marker-end="url(#s)"/>'
        )
    for uid, (x, y) in pos.items():
        o += _desenhar_no(g.nos[uid], x, y, larg, alt)
    o.append("</svg>")
    return "\n".join(o)
