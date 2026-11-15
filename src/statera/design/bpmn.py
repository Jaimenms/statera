"""Leitura, validação e visualização dos processos BPMN 2.0.

Os arquivos .bpmn em <unidade>/2-design/processes/ são gerados dos YAML de processo
(processos.py) e abrem em qualquer ferramenta BPMN (Camunda Modeler, bpmn.io).
Atributos no namespace `statera` ligam cada tarefa ao repositório e à execução:

    statera:type            automatic | semi-automatic | manual
    statera:gsbpm           subprocesso GSBPM (ex.: 5.7, 6.4;7.2, 5.1-5.7)
    statera:duracaoMin      duração esperada por edição, em minutos
    statera:implementacao   py:<modulo>.<funcao> | dbt:build
    statera:modelos         modelos dbt a construir (com tudo o que vem antes) em dbt:build
    statera:excecao         "true" para tarefas fora do caminho normal (não entram no esforço)
    statera:aguarda         (em evento intermediário) função que diz se a espera terminou
    statera:papel           (na raia) papel de governance/roles.yaml

Nos fluxos que saem de gateways, `conditionExpression` (${situacao == 'ok'}) diz qual caminho o
executor segue (statera.execucao).

Este módulo:
  - renderiza SVG a partir do DI do próprio arquivo (sem Node nem navegador), para ser exibido
    no GitHub e no preview de Markdown do VS Code;
  - gera um visualizador HTML interativo com bpmn-js;
  - resume tarefas, tempos e tipos (base da comparação as-is x to-be);
  - valida as ligações entre o diagrama e o repositório.
"""

from __future__ import annotations

import html
import importlib
import re
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from statera import gerado
from statera.projeto import atual

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "statera": "urn:statera:bpmn:1.0",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
EXT = f"{{{NS['statera']}}}"
TAREFAS = {
    "task",
    "userTask",
    "serviceTask",
    "manualTask",
    "scriptTask",
    "sendTask",
    "receiveTask",
    "businessRuleTask",
    "callActivity",
    "subProcess",
}
EVENTOS = {"startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}
GATEWAYS = {"exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway"}
CORES_TIPO = {
    "automatic": ("#E3F4E8", "#2E7D4F"),
    "semi-automatic": ("#FFF4D6", "#A6750A"),
    "manual": ("#FDE4E1", "#B03A2E"),
    None: ("#FFFFFF", "#404040"),
}
FONTE = 'font-family="Helvetica, Arial, sans-serif"'
BPMN_JS_VERSAO = "18.30.1"
Caixa = tuple[float, float, float, float]


@dataclass
class Elemento:
    id: str
    tag: str
    nome: str
    attrs: dict[str, str] = field(default_factory=dict)
    lane: str | None = None
    timer: bool = False
    documentacao: str = ""
    mensagem: bool = False

    @property
    def tipo(self) -> str | None:
        return self.attrs.get("type")

    @property
    def duracao(self) -> float:
        return float(self.attrs.get("durationMin", 0) or 0)


@dataclass
class Processo:
    caminho: Path
    nome: str
    elementos: dict[str, Elemento]
    lanes: dict[str, dict]
    formas: dict[str, Caixa]
    rotulos: dict[str, Caixa]
    arestas: dict[str, list[tuple[float, float]]]
    fluxos: dict[str, dict]

    @property
    def tarefas(self) -> list[Elemento]:
        return [e for e in self.elementos.values() if e.tag in TAREFAS]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _caixa(no: ET.Element | None) -> Caixa | None:
    if no is None:
        return None
    return tuple(float(no.get(k)) for k in ("x", "y", "width", "height"))  # type: ignore[return-value]


CONDICAO = re.compile(r"situacao\s*==\s*'([\w-]+)'")


def _condicao(fluxo: ET.Element) -> str:
    """Condição do fluxo, a partir de <conditionExpression>${situacao == 'ok'}</conditionExpression>."""
    expressao = fluxo.find("bpmn:conditionExpression", NS)
    achado = CONDICAO.search(expressao.text or "") if expressao is not None else None
    return achado.group(1) if achado else ""


def _ler_lanes(processo: ET.Element) -> dict[str, dict]:
    lanes = {}
    for lane in processo.iter(f"{{{NS['bpmn']}}}lane"):
        lanes[lane.get("id")] = {
            "name": lane.get("name", ""),
            "role": lane.get(f"{EXT}papel"),
            "nos": [r.text for r in lane.findall("bpmn:flowNodeRef", NS)],
        }
    return lanes


def _ler_associacoes(no: ET.Element, fluxos: dict[str, dict]) -> None:
    for assoc in no.findall("bpmn:dataInputAssociation", NS):
        origem = assoc.find("bpmn:sourceRef", NS).text
        fluxos[assoc.get("id")] = {"tag": "dataInput", "from": origem, "to": no.get("id"), "name": ""}
    for assoc in no.findall("bpmn:dataOutputAssociation", NS):
        destino = assoc.find("bpmn:targetRef", NS).text
        fluxos[assoc.get("id")] = {"tag": "dataOutput", "from": no.get("id"), "to": destino, "name": ""}


def _ler_elementos(processo: ET.Element, elementos: dict, fluxos: dict) -> None:
    for filho in processo:
        tag = _local(filho.tag)
        if tag in ("laneSet", "dataObject"):
            continue
        if tag in ("sequenceFlow", "association"):
            fluxos[filho.get("id")] = {
                "tag": tag,
                "from": filho.get("sourceRef"),
                "to": filho.get("targetRef"),
                "name": filho.get("name", ""),
                "condition": _condicao(filho),
            }
            continue
        doc = filho.find("bpmn:documentation", NS)
        elementos[filho.get("id")] = Elemento(
            id=filho.get("id"),
            tag=tag,
            nome=filho.get("name", ""),
            attrs={k[len(EXT) :]: v for k, v in filho.attrib.items() if k.startswith(EXT)},
            timer=filho.find("bpmn:timerEventDefinition", NS) is not None,
            mensagem=filho.find("bpmn:messageEventDefinition", NS) is not None,
            documentacao=(doc.text or "").strip() if doc is not None else "",
        )
        _ler_associacoes(filho, fluxos)


def ler(caminho: Path) -> Processo:
    raiz = ET.parse(caminho).getroot()
    participante = raiz.find(".//bpmn:participant", NS)
    nome = participante.get("name") if participante is not None and participante.get("name") else caminho.stem
    elementos: dict[str, Elemento] = {}
    lanes: dict[str, dict] = {}
    fluxos: dict[str, dict] = {}
    for processo in raiz.findall("bpmn:process", NS):
        lanes |= _ler_lanes(processo)
        _ler_elementos(processo, elementos, fluxos)
    for lid, lane in lanes.items():
        for ref in lane["nos"]:
            if ref in elementos:
                elementos[ref].lane = lid

    formas, rotulos, arestas = {}, {}, {}
    for forma in raiz.iter(f"{{{NS['bpmndi']}}}BPMNShape"):
        formas[forma.get("bpmnElement")] = _caixa(forma.find("dc:Bounds", NS))
        if rot := _caixa(forma.find("bpmndi:BPMNLabel/dc:Bounds", NS)):
            rotulos[forma.get("bpmnElement")] = rot
    for aresta in raiz.iter(f"{{{NS['bpmndi']}}}BPMNEdge"):
        pontos = [(float(w.get("x")), float(w.get("y"))) for w in aresta.findall("di:waypoint", NS)]
        arestas[aresta.get("bpmnElement")] = pontos
        if rot := _caixa(aresta.find("bpmndi:BPMNLabel/dc:Bounds", NS)):
            rotulos[aresta.get("bpmnElement")] = rot
    return Processo(caminho, nome, elementos, lanes, formas, rotulos, arestas, fluxos)


def todos() -> list[Processo]:
    return [ler(c.with_suffix(".bpmn")) for c in atual().processos() if c.with_suffix(".bpmn").exists()]


def resumo(p: Processo) -> dict:
    """Horas por edição no caminho normal (tarefas com statera:excecao="true" ficam de fora)."""
    horas = {"manual": 0.0, "semi-automatic": 0.0, "automatic": 0.0}
    for t in p.tarefas:
        if t.tipo in horas and t.attrs.get("exception") != "true":
            horas[t.tipo] += t.duracao / 60
    return {
        "tarefas": len(p.tarefas),
        **{f"{k}_h": v for k, v in horas.items()},
        "total_h": sum(horas.values()),
    }


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def _texto(
    x: float,
    y: float,
    conteudo: str,
    largura_chars: int,
    tamanho: float = 11,
    ancora: str = "middle",
    peso: str = "normal",
    cor: str = "#1F1F1F",
    vertical: str = "center",
) -> str:
    linhas = textwrap.wrap(conteudo, largura_chars) or [""]
    altura = tamanho * 1.2
    y0 = y - (len(linhas) - 1) * altura / 2 if vertical == "center" else y
    spans = "".join(
        f'<tspan x="{x:.1f}" y="{y0 + i * altura:.1f}">{html.escape(t)}</tspan>' for i, t in enumerate(linhas)
    )
    return (
        f'<text {FONTE} font-size="{tamanho}" font-weight="{peso}" fill="{cor}" '
        f'text-anchor="{ancora}" dominant-baseline="middle">{spans}</text>'
    )


def _texto_vertical(x: float, y: float, conteudo: str, max_chars: int, tamanho: float, peso: str) -> list[str]:
    linhas = textwrap.wrap(conteudo, max(8, max_chars))[:2]
    saida = []
    for i, linha in enumerate(linhas):
        dx = 15 + (i - (len(linhas) - 1) / 2) * (tamanho + 2)
        saida.append(
            f'<text transform="translate({x + dx + 3},{y}) rotate(-90)" text-anchor="middle" '
            f'dominant-baseline="middle" font-weight="{peso}" {FONTE} font-size="{tamanho}">'
            f"{html.escape(linha)}</text>"
        )
    return saida


def _seta(pontos: list[tuple[float, float]], de_dados: bool) -> str:
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pontos)
    if de_dados:
        estilo = 'stroke="#666" stroke-dasharray="4 3" marker-end="url(#seta-dado)"'
    else:
        estilo = 'stroke="#333" marker-end="url(#seta)"'
    return f'<path d="{d}" fill="none" stroke-width="1.4" {estilo}/>'


def _icone_tarefa(tag: str, x: float, y: float) -> str:
    if tag == "serviceTask":  # engrenagem simplificada
        return (
            f'<circle cx="{x + 12}" cy="{y + 12}" r="6" fill="none" stroke="#2E7D4F" stroke-width="2" '
            f'stroke-dasharray="3 1.6"/><circle cx="{x + 12}" cy="{y + 12}" r="2" fill="#2E7D4F"/>'
        )
    if tag == "userTask":  # pessoa
        return (
            f'<circle cx="{x + 12}" cy="{y + 9}" r="3.5" fill="none" stroke="#444" stroke-width="1.5"/>'
            f'<path d="M{x + 5},{y + 19} q7,-9 14,0" fill="none" stroke="#444" stroke-width="1.5"/>'
        )
    if tag == "manualTask":  # mão (simplificada)
        return (
            f'<path d="M{x + 5},{y + 16} h8 v-8 M{x + 9},{y + 16} v-9 M{x + 13},{y + 16} v-8" '
            f'fill="none" stroke="#B03A2E" stroke-width="1.5"/>'
        )
    return ""


def _desenhar_tarefa(el: Elemento, caixa: Caixa) -> list[str]:
    x, y, w, h = caixa
    fundo, borda = CORES_TIPO.get(el.tipo, CORES_TIPO[None])
    partes = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fundo}" stroke="{borda}" stroke-width="1.6"/>',
        _icone_tarefa(el.tag, x, y),
        _texto(x + w / 2, y + h / 2 + 2, el.nome, 22, 10.5),
    ]
    if el.attrs.get("gsbpm"):
        partes.append(_texto(x + w - 5, y + 10, f"GSBPM {el.attrs['gsbpm']}", 20, 8.5, "end", cor="#555"))
    if el.duracao:
        dur = f"{el.duracao / 60:.1f} h" if el.duracao >= 60 else f"{el.duracao:.0f} min"
        partes.append(_texto(x + w - 5, y + h - 8, f"~{dur}", 12, 8.5, "end", "bold", borda))
    return partes


def _desenhar_evento(el: Elemento, caixa: Caixa) -> list[str]:
    x, y, w, h = caixa
    cx, cy, r = x + w / 2, y + h / 2, w / 2
    espessura = 3.5 if el.tag == "endEvent" else 1.6
    partes = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFF" stroke="#333" stroke-width="{espessura}"/>']
    if el.tag.startswith("intermediate"):
        partes.append(f'<circle cx="{cx}" cy="{cy}" r="{r - 3}" fill="none" stroke="#333" stroke-width="1.2"/>')
    if el.timer:
        partes.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r - 6}" fill="none" stroke="#333"/>'
            f'<path d="M{cx},{cy - r + 9} L{cx},{cy} L{cx + 6},{cy}" stroke="#333" fill="none"/>'
        )
    if el.mensagem:  # envelope
        partes.append(
            f'<rect x="{cx - 8}" y="{cy - 5.5}" width="16" height="11" fill="none" stroke="#333"/>'
            f'<path d="M{cx - 8},{cy - 5.5} L{cx},{cy + 1} L{cx + 8},{cy - 5.5}" fill="none" stroke="#333"/>'
        )
    return partes


def _desenhar_gateway(el: Elemento, caixa: Caixa) -> list[str]:
    x, y, w, h = caixa
    cx, cy = x + w / 2, y + h / 2
    pontos = f"{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}"
    partes = [f'<polygon points="{pontos}" fill="#FFF" stroke="#333" stroke-width="1.6"/>']
    if el.tag == "exclusiveGateway":
        marca = f"M{cx - 8},{cy - 8} L{cx + 8},{cy + 8} M{cx + 8},{cy - 8} L{cx - 8},{cy + 8}"
        partes.append(f'<path d="{marca}" stroke="#333" stroke-width="3"/>')
    elif el.tag == "parallelGateway":
        marca = f"M{cx},{cy - 11} V{cy + 11} M{cx - 11},{cy} H{cx + 11}"
        partes.append(f'<path d="{marca}" stroke="#333" stroke-width="3"/>')
    return partes


def _desenhar_dado(el: Elemento, caixa: Caixa) -> list[str]:
    x, y, w, h = caixa
    estilo = 'fill="#F3F6FB" stroke="#4A6FA5" stroke-width="1.4"'
    if el.tag == "dataStoreReference":
        arco = f"a{w / 2},8 0 0 0"
        d = f"M{x},{y + 8} v{h - 16} {arco} {w},0 v-{h - 16} {arco} -{w},0 {arco} {w},0"
    else:
        d = f"M{x},{y} h{w - 10} l10,10 v{h - 10} h-{w} z"
    return [f'<path d="{d}" {estilo}/>']


def _desenhar_elemento(p: Processo, eid: str, el: Elemento) -> list[str]:
    caixa = p.formas[eid]
    if el.tag in TAREFAS:
        return _desenhar_tarefa(el, caixa)
    if el.tag in EVENTOS:
        partes = _desenhar_evento(el, caixa)
    elif el.tag in GATEWAYS:
        partes = _desenhar_gateway(el, caixa)
    elif el.tag in ("dataStoreReference", "dataObjectReference"):
        partes = _desenhar_dado(el, caixa)
    else:
        partes = []
    if el.nome:
        x, y, w, h = caixa
        rx, ry, rw, _ = p.rotulos.get(eid, (x + w / 2 - 50, y + h + 4, 100, 27))
        partes.append(_texto(rx + rw / 2, ry + 6, el.nome, 22, 10, vertical="top"))
    return partes


def _desenhar_pool_e_raias(p: Processo) -> list[str]:
    partes = []
    for eid, (x, y, w, h) in p.formas.items():
        if eid.startswith("Pool"):
            partes.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#FAFAFA" stroke="#333" '
                f'stroke-width="1.5"/><line x1="{x + 30}" y1="{y}" x2="{x + 30}" y2="{y + h}" stroke="#333"/>'
            )
            partes += _texto_vertical(x, y + h / 2, p.nome, int(h / 7.8), 11.5, "bold")
    for lid, lane in p.lanes.items():
        if lid in p.formas:
            x, y, w, h = p.formas[lid]
            partes.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#999"/>'
                f'<line x1="{x + 30}" y1="{y}" x2="{x + 30}" y2="{y + h}" stroke="#999"/>'
            )
            partes += _texto_vertical(x, y + h / 2, lane["name"], int(h / 6.4), 10, "normal")
    return partes


def _desenhar_arestas(p: Processo) -> list[str]:
    partes = []
    for fid, pontos in p.arestas.items():
        fluxo = p.fluxos.get(fid, {})
        partes.append(_seta(pontos, fluxo.get("tag") in ("dataInput", "dataOutput", "association")))
        if fluxo.get("name") and fid in p.rotulos:
            x, y, w, h = p.rotulos[fid]
            partes.append(_texto(x + w / 2, y + h / 2, fluxo["name"], 12, 10.5, peso="bold", cor="#333"))
    return partes


def _desenhar_legenda(p: Processo, y: float) -> list[str]:
    partes = [_texto(20, y, "Legenda:", 20, 11, "start", "bold", vertical="top")]
    tipos = [("automatic", "automática"), ("semi-automatic", "semiautomática"), ("manual", "manual")]
    for i, (tipo, rotulo) in enumerate(tipos):
        fundo, borda = CORES_TIPO[tipo]
        x = 90 + i * 150
        partes.append(f'<rect x="{x}" y="{y - 7}" width="26" height="16" rx="4" fill="{fundo}" stroke="{borda}"/>')
        partes.append(_texto(x + 34, y + 1, rotulo, 20, 11, "start"))
    r = resumo(p)
    texto = (
        f"Tempo de trabalho por edição: {r['manual_h']:.1f} h manual, {r['semi-automatic_h']:.1f} h "
        f"semiautomático, {r['automatic_h']:.1f} h automático (durações modeladas, caminho normal)."
    )
    partes.append(_texto(20, y + 26, texto, 200, 11, "start", vertical="top"))
    return partes


def para_svg(p: Processo) -> str:
    xs = [x + w for x, _, w, _ in p.formas.values()] + [x for pts in p.arestas.values() for x, _ in pts]
    ys = [y + h for _, y, _, h in p.formas.values()] + [y for pts in p.arestas.values() for _, y in pts]
    largura, altura = max(xs) + 30, max(ys) + 110
    marcadores = (
        '<defs><marker id="seta" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker>'
        '<marker id="seta-dado" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10" fill="none" stroke="#666"/></marker></defs>'
    )
    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura:.0f} {altura:.0f}" '
        f'width="{largura:.0f}" height="{altura:.0f}" role="img" aria-label="{html.escape(p.nome)}">',
        marcadores,
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        _texto(20, 40, p.nome, 140, 16, "start", "bold", vertical="top"),
        *_desenhar_pool_e_raias(p),
        *_desenhar_arestas(p),
    ]
    for eid, el in p.elementos.items():
        if eid in p.formas:
            o += _desenhar_elemento(p, eid, el)
    o += _desenhar_legenda(p, altura - 60)
    o.append("</svg>")
    return "\n".join(o)


def html_visualizador(p: Processo) -> str:
    xml = p.caminho.read_text(encoding="utf-8").replace("</script", "<\\/script")
    cdn = f"https://unpkg.com/bpmn-js@{BPMN_JS_VERSAO}/dist"
    origem = html.escape(atual().rel(p.caminho))
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(p.nome)}</title>
<link rel="stylesheet" href="{cdn}/assets/diagram-js.css">
<link rel="stylesheet" href="{cdn}/assets/bpmn-js.css">
<style>html,body{{margin:0;height:100%;font-family:Helvetica,Arial,sans-serif;background:#fff}}
#topo{{padding:8px 16px;border-bottom:1px solid #ddd}} #canvas{{height:calc(100% - 50px)}}</style>
</head>
<body>
<div id="topo"><strong>{html.escape(p.nome)}</strong> — arraste para mover, role para ampliar.
Fonte: <code>{origem}</code></div>
<div id="canvas"></div>
<script src="{cdn}/bpmn-navigated-viewer.production.min.js"></script>
<script id="diagrama" type="application/xml">{xml}</script>
<script>
  const viewer = new BpmnJS({{ container: '#canvas' }});
  viewer.importXML(document.getElementById('diagrama').textContent)
    .then(() => viewer.get('canvas').zoom('fit-viewport'))
    .catch(err => document.getElementById('canvas').innerText = 'Erro: ' + err.message);
</script>
</body>
</html>
"""


def destino_svg(p: Processo) -> Path:
    projeto = atual()
    unidade = projeto.unidade_de(p.caminho)
    prefixo = unidade.nome if unidade else "governance"
    return projeto.docs / "processes" / f"{prefixo}__{p.caminho.stem}.svg"


def renderizar_todos() -> list[Path]:
    gerados = []
    for p in todos():
        svg = destino_svg(p)
        svg.parent.mkdir(parents=True, exist_ok=True)
        aviso = gerado.cabecalho(
            "statera bpmn", atual().rel(p.caminho), regenerar="statera bpmn", ci=False, estilo="xml"
        )
        svg.write_text(aviso + para_svg(p), encoding="utf-8")
        visualizador = svg.with_suffix(".html")
        visualizador.write_text(gerado.em_html(html_visualizador(p), aviso), encoding="utf-8")
        gerados += [svg, visualizador]
    return gerados


# ---------------------------------------------------------------------------
# Validação das ligações BPMN -> repositório
# ---------------------------------------------------------------------------

PADRAO_GSBPM = re.compile(r"^\d\.\d+([-;]\d\.\d+)*$")
# operadores de grafo do seletor do dbt: +modelo (ancestrais), modelo+ (descendentes), @modelo, 2+modelo
OPERADORES_DBT = re.compile(r"^(?:@|\d*\+)?(?P<name>[^+@]+?)(?:\+\d*)?$")


def nome_do_seletor(seletor: str) -> str:
    """'+int_x' -> 'int_x' (tira os operadores de grafo do seletor do dbt)."""
    achado = OPERADORES_DBT.match(seletor.strip())
    return achado.group("name") if achado else seletor.strip()


def _validar_implementacao(nome: str, t: Elemento, modelos_dbt: set[str]) -> list[str]:
    impl = t.attrs.get("implementation")
    if not impl:
        if t.tipo == "automatic":
            return [f"{nome}: {t.id} é automática mas não declara statera:implementacao"]
        return []
    if impl.startswith("py:"):
        modulo, _, funcao = impl[3:].rpartition(".")
        try:
            existe = hasattr(importlib.import_module(modulo), funcao)
        except ImportError:
            return [f"{nome}: {t.id} aponta para módulo inexistente {modulo}"]
        return [] if existe else [f"{nome}: {t.id} aponta para {impl}, que não existe"]
    if impl.startswith("dbt:"):
        alvo = impl[4:]
        lista = t.attrs.get("models", "").split(",") if alvo == "build" else [alvo]
        return [
            f"{nome}: {t.id} aponta para modelo dbt inexistente '{m}'"
            for m in lista
            if m.strip() and nome_do_seletor(m) not in modelos_dbt
        ]
    return [f"{nome}: {t.id} com implementação em formato desconhecido '{impl}'"]


def validar(p: Processo, modelos_dbt: set[str], papeis: set[str]) -> list[str]:
    nome = atual().rel(p.caminho)
    erros = [
        f"{nome}: raia {lid} com papel desconhecido '{lane['role']}'"
        for lid, lane in p.lanes.items()
        if lane["role"] not in papeis
    ]
    for el in p.elementos.values():
        if (aguarda := el.attrs.get("awaits")) and el.tag not in TAREFAS:
            modulo, _, funcao = aguarda[3:].rpartition(".")
            try:
                existe = hasattr(importlib.import_module(modulo), funcao)
            except ImportError:
                existe = False
            if not existe:
                erros.append(f"{nome}: {el.id} aguarda {aguarda}, que não existe")
    for t in p.tarefas:
        if t.tipo not in ("automatic", "semi-automatic", "manual"):
            erros.append(f"{nome}: {t.id} sem statera:tipo válido")
        if not PADRAO_GSBPM.match(t.attrs.get("gsbpm", "")):
            erros.append(f"{nome}: {t.id} sem statera:gsbpm válido")
        if not t.duracao:
            erros.append(f"{nome}: {t.id} sem statera:duracaoMin")
        erros += _validar_implementacao(nome, t, modelos_dbt)
    erros += [f"{nome}: {eid} sem forma no diagrama (DI)" for eid in p.elementos if eid not in p.formas]
    return erros
