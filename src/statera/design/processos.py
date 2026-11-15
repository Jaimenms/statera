"""Processos descritos em YAML (espelho 1:1 do BPMN 2.0) <-> arquivos .bpmn padrão.

Não existe padrão estabelecido de YAML para BPMN (ver ADR 0007). Por isso o YAML deste
repositório é um espelho direto da especificação: os tipos de elemento usam os nomes do BPMN 2.0
(`startEvent`, `userTask`, `exclusiveGateway`, `dataStoreReference`…), e o que é próprio do padrão
fica isolado no bloco `statera`. A saída é sempre BPMN 2.0 XML padrão, com DI (coordenadas), que
abre em qualquer ferramenta.

    <unidade>/2-design/processes/<nome>.yaml  --statera bpmn-->           <nome>.bpmn (gerado, ao lado)
    <unidade>/2-design/processes/<nome>.bpmn  --statera bpmn import-->  <nome>.yaml

Fluxos que saem de gateways levam `condicao` (ok, falha, aprovado, reprovado...), gravada no
BPMN como conditionExpression: é por ela que o executor (statera.execucao) escolhe o caminho.

Layout automático: colunas pelo caminho mais longo no grafo sem os fluxos de retorno; uma raia
por linha; depósitos de dados abaixo da tarefa que os usa; retornos roteados por baixo.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from jsonschema import Draft202012Validator

from statera import gerado
from statera.design import bpmn
from statera.projeto import ESQUEMAS_STATERA, atual

COL, X0, Y0 = 175, 110, 80
ALTURA_RAIA, ALTURA_RAIA_COM_DADOS = 150, 230
TAMANHOS = {
    "tarefa": (140, 80),
    "event": (36, 36),
    "gateway": (50, 50),
    "dataStoreReference": (50, 50),
    "dataObjectReference": (36, 50),
}
ATRIBUTOS = ("type", "gsbpm", "durationMin", "implementation", "models", "exception", "awaits")
Ponto = tuple[float, float]


def _forma(tipo: str) -> str:
    if tipo in bpmn.TAREFAS:
        return "tarefa"
    if tipo in bpmn.EVENTOS:
        return "event"
    if tipo in bpmn.GATEWAYS:
        return "gateway"
    return tipo


# ---------------------------------------------------------------------------
# Leitura e validação estrutural do YAML
# ---------------------------------------------------------------------------


@cache
def _validador() -> Draft202012Validator:
    return Draft202012Validator(json.loads((ESQUEMAS_STATERA / "process.schema.json").read_text()))


def carregar(caminho: Path) -> dict:
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


def fontes() -> list[Path]:
    return atual().processos()


def _fluxos(proc: dict) -> list[dict]:
    return [{"id": f.get("id") or f"Flow_{f['from']}__{f['to']}", **f} for f in proc["flows"]]


def validar_estrutura(proc: dict) -> list[str]:
    """Erros de esquema e de referência (ids inexistentes, dados sem tarefa, ids repetidos)."""
    erros = [f"{'/'.join(map(str, e.absolute_path)) or '(raiz)'}: {e.message}" for e in _validador().iter_errors(proc)]
    if erros:
        return erros
    raias = {r["id"] for r in proc["lanes"]}
    nos = {e["id"] for e in proc["elements"]}
    dados = {d["id"] for d in proc.get("data", [])}
    todos = [r["id"] for r in proc["lanes"]] + [e["id"] for e in proc["elements"]] + list(dados)
    erros += [f"id repetido: {i}" for i in sorted({i for i in todos if todos.count(i) > 1})]
    for e in proc["elements"]:
        if e["lane"] not in raias:
            erros.append(f"{e['id']}: raia '{e['lane']}' não existe")
        erros += [
            f"{e['id']}: dado '{d}' não existe" for d in e.get("inputs", []) + e.get("outputs", []) if d not in dados
        ]
    for f in _fluxos(proc):
        erros += [f"fluxo {f['id']}: '{f[k]}' não existe" for k in ("from", "to") if f[k] not in nos]
    usados = {d for e in proc["elements"] for d in e.get("inputs", []) + e.get("outputs", [])}
    erros += [f"dado '{d}' não está associado a nenhuma tarefa" for d in sorted(dados - usados)]
    if not any(e["type"] == "startEvent" for e in proc["elements"]):
        erros.append("processo sem startEvent")
    return erros


# ---------------------------------------------------------------------------
# Layout automático
# ---------------------------------------------------------------------------


@dataclass
class Layout:
    geo: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    raias: dict[str, tuple[float, float]] = field(default_factory=dict)  # id -> (topo, altura)
    largura: float = 0
    rotas: dict[str, list[Ponto]] = field(default_factory=dict)
    rotulos: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)


def _retornos(proc: dict, fluxos: list[dict]) -> set[str]:
    """Fluxos de retorno (arestas para um ancestral na busca em profundidade a partir do início)."""
    saida: dict[str, list[dict]] = {}
    for f in fluxos:
        saida.setdefault(f["from"], []).append(f)
    retornos, visitados, pilha_ativa = set(), set(), set()

    def visitar(no: str) -> None:
        visitados.add(no)
        pilha_ativa.add(no)
        for f in saida.get(no, []):
            if f["to"] in pilha_ativa:
                retornos.add(f["id"])
            elif f["to"] not in visitados:
                visitar(f["to"])
        pilha_ativa.discard(no)

    for e in proc["elements"]:
        if e["type"] == "startEvent" and e["id"] not in visitados:
            visitar(e["id"])
    return retornos


def _colunas(proc: dict, fluxos: list[dict], retornos: set[str]) -> dict[str, int]:
    pais: dict[str, list[str]] = {e["id"]: [] for e in proc["elements"]}
    for f in fluxos:
        if f["id"] not in retornos:
            pais[f["to"]].append(f["from"])
    raia = {e["id"]: e["lane"] for e in proc["elements"]}
    fixas = {e["id"]: e["column"] for e in proc["elements"] if "column" in e}
    coluna: dict[str, int] = {}
    ocupado: set[tuple[str, int]] = set()
    pendentes = [e["id"] for e in proc["elements"]]
    while pendentes:  # ordem topológica estável
        prontos = [n for n in pendentes if all(p in coluna for p in pais[n])]
        if not prontos:
            raise ValueError(f"ciclo sem retorno identificável entre {pendentes}")
        for n in prontos:
            c = fixas.get(n, max((coluna[p] + 1 for p in pais[n]), default=0))
            while (raia[n], c) in ocupado:
                c += 1
            coluna[n] = c
            ocupado.add((raia[n], c))
            pendentes.remove(n)
    return coluna


def _posicionar(proc: dict, colunas: dict[str, int], retornos_alvo: set[str]) -> Layout:
    lay = Layout()
    com_dados = {e["lane"] for e in proc["elements"] if e.get("inputs") or e.get("outputs")}
    y = Y0
    for r in proc["lanes"]:
        altura = ALTURA_RAIA_COM_DADOS if r["id"] in com_dados else ALTURA_RAIA
        lay.raias[r["id"]] = (y, altura)
        y += altura
    for e in proc["elements"]:
        w, h = TAMANHOS[_forma(e["type"])]
        topo, altura = lay.raias[e["lane"]]
        cy = topo + (70 if e["lane"] in com_dados else altura / 2)
        cx = X0 + colunas[e["id"]] * COL + 70
        lay.geo[e["id"]] = (cx - w / 2, cy - h / 2, w, h)
    tipos_dado = {d["id"]: d["type"] for d in proc.get("data", [])}
    for e in proc["elements"]:
        associados = e.get("inputs", []) + e.get("outputs", [])
        for i, d in enumerate(associados):
            if d in lay.geo:
                continue
            w, h = TAMANHOS[tipos_dado[d]]
            tx, _, tw, _ = lay.geo[e["id"]]
            desvio = -35 if e["id"] in retornos_alvo else 0  # libera a base para o retorno
            cx = tx + tw / 2 + desvio + (i - (len(associados) - 1) / 2) * 60
            topo, _ = lay.raias[e["lane"]]
            lay.geo[d] = (cx - w / 2, topo + 180 - h / 2, w, h)
    lay.largura = X0 + (max(colunas.values()) + 1) * COL + 40
    return lay


def _rota_retorno(a: tuple, b: tuple, de_gateway: bool) -> list[Ponto]:
    """Fluxo de volta a um elemento anterior: sempre por baixo, entrando pela base do alvo."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, bcx = ax + aw / 2, bx + bw / 2
    if abs((ay + ah / 2) - (by + bh / 2)) < 1:  # mesma raia: passa por baixo dos dois
        fundo = max(ay + ah, by + bh) + 22
        return [(acx, ay + ah), (acx, fundo), (bcx, fundo), (bcx, by + bh)]
    if by > ay:  # alvo em raia abaixo: entra por cima
        return [(acx, ay + ah), (acx, by - 20), (bcx, by - 20), (bcx, by)]
    entrada = bx + bw - 30 if bh > 50 else bcx  # à direita da base, deixando o centro para dados
    if de_gateway:  # sai pelo vértice de cima
        altura = by + bh + 28
        return [(acx, ay), (acx, altura), (entrada, altura), (entrada, by + bh)]
    altura = ay + ah + 25  # tarefa: sai por baixo, para não cruzar a própria entrada
    return [(acx, ay + ah), (acx, altura), (entrada, altura), (entrada, by + bh)]


def _rota_avanco(a: tuple, b: tuple, de_gateway: bool) -> list[Ponto]:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy, bcy = ax + aw / 2, ay + ah / 2, by + bh / 2
    if abs(acy - bcy) < 1:
        return [(ax + aw, acy), (bx, bcy)]
    if de_gateway:  # gateway para outra raia: sai pelo vértice de cima ou de baixo
        return [(acx, ay + ah if by > ay else ay), (acx, bcy), (bx, bcy)]
    if bx > ax + aw:
        meio = (ax + aw + bx) / 2
        return [(ax + aw, acy), (meio, acy), (meio, bcy), (bx, bcy)]
    bcx = bx + bw / 2
    return [(acx, ay + ah), (bcx, by)] if by > ay else [(acx, ay), (bcx, by + bh)]


def _rota(lay: Layout, f: dict, retorno: bool, gateways: set[str]) -> list[Ponto]:
    a, b = lay.geo[f["from"]], lay.geo[f["to"]]
    de_gateway = f["from"] in gateways
    return _rota_retorno(a, b, de_gateway) if retorno else _rota_avanco(a, b, de_gateway)


def _rotulo_fluxo(pontos: list[Ponto]) -> tuple[float, float, float, float]:
    (x0, y0), (x1, y1) = pontos[0], pontos[1]
    if abs(x0 - x1) < 1:
        return (x0 + 6, y0 + 4 if y1 > y0 else y0 - 20, 30, 14)
    return (x0 + 6, y0 - 18, 30, 14)


def _rotulo_no(caixa, ocupados: set[str]) -> tuple[float, float, float, float]:
    """Rótulo de evento ou gateway no primeiro lado livre de saídas: baixo, cima, direita, esquerda."""
    x, y, w, h = caixa
    if "baixo" not in ocupados:
        return (x + w / 2 - 50, y + h + 4, 100, 27)
    if "cima" not in ocupados:
        return (x + w / 2 - 50, y - 31, 100, 27)
    if "direita" not in ocupados:
        return (x + w + 4, y + h / 2 - 7, 70, 14)
    return (x - 105, y - 30, 100, 27)


def calcular_layout(proc: dict) -> Layout:
    fluxos = _fluxos(proc)
    retornos = _retornos(proc, fluxos)
    colunas = _colunas(proc, fluxos, retornos)
    alvos_retorno = {f["to"] for f in fluxos if f["id"] in retornos}
    lay = _posicionar(proc, colunas, alvos_retorno)
    gateways = {e["id"] for e in proc["elements"] if e["type"] in bpmn.GATEWAYS}
    for f in fluxos:
        lay.rotas[f["id"]] = _rota(lay, f, f["id"] in retornos, gateways)
        if f.get("name"):
            lay.rotulos[f["id"]] = _rotulo_fluxo(lay.rotas[f["id"]])
    for e in proc["elements"]:
        if e["type"] in bpmn.TAREFAS:
            continue
        caixa = lay.geo[e["id"]]
        x, y, w, h = caixa
        partidas = [lay.rotas[f["id"]][0] for f in fluxos if f["from"] == e["id"]]
        ocupados = set()
        for px, py in partidas:
            if abs(py - (y + h)) < 1:
                ocupados.add("baixo")
            elif abs(py - y) < 1:
                ocupados.add("cima")
            elif abs(px - (x + w)) < 1:
                ocupados.add("direita")
        lay.rotulos[e["id"]] = _rotulo_no(caixa, ocupados)
    for d in proc.get("data", []):
        x, y, w, h = lay.geo[d["id"]]
        lay.rotulos[d["id"]] = (x + w / 2 - 50, y + h + 4, 100, 27)
    return lay


# ---------------------------------------------------------------------------
# Geração do BPMN 2.0 XML
# ---------------------------------------------------------------------------


def _atributos_ext(ext: dict) -> str:
    partes = []
    for chave in ATRIBUTOS:
        if chave not in ext:
            continue
        valor = ext[chave]
        if isinstance(valor, list):
            valor = ",".join(valor)
        elif isinstance(valor, bool):
            valor = str(valor).lower()
        elif isinstance(valor, float) and valor.is_integer():
            valor = int(valor)
        partes.append(f' statera:{chave}="{escape(str(valor))}"')
    return "".join(partes)


def _xml_elemento(e: dict, fluxos: list[dict]) -> list[str]:
    nome = f' name="{escape(e["name"])}"' if e.get("name") else ""
    linhas = [f'    <bpmn:{e["type"]} id="{e["id"]}"{nome}{_atributos_ext(e.get("statera", {}))}>']
    if e.get("documentation"):
        linhas.append(f"      <bpmn:documentation>{escape(e['documentation'])}</bpmn:documentation>")
    linhas += [f"      <bpmn:incoming>{f['id']}</bpmn:incoming>" for f in fluxos if f["to"] == e["id"]]
    linhas += [f"      <bpmn:outgoing>{f['id']}</bpmn:outgoing>" for f in fluxos if f["from"] == e["id"]]
    if e.get("inputs"):
        linhas.append(f'      <bpmn:property id="Property_{e["id"]}" name="__targetRef_placeholder" />')
    for i, d in enumerate(e.get("inputs", []), start=1):
        linhas.append(
            f'      <bpmn:dataInputAssociation id="DataIn_{e["id"]}_{i}"><bpmn:sourceRef>{d}</bpmn:sourceRef>'
            f"<bpmn:targetRef>Property_{e['id']}</bpmn:targetRef></bpmn:dataInputAssociation>"
        )
    for i, d in enumerate(e.get("outputs", []), start=1):
        linhas.append(
            f'      <bpmn:dataOutputAssociation id="DataOut_{e["id"]}_{i}">'
            f"<bpmn:targetRef>{d}</bpmn:targetRef></bpmn:dataOutputAssociation>"
        )
    if e.get("event"):
        linhas.append(f'      <bpmn:{e["event"]}EventDefinition id="Def_{e["id"]}" />')
    linhas.append(f"    </bpmn:{e['type']}>")
    return linhas


def _xml_dado(d: dict) -> list[str]:
    ativo = f' statera:ativo="{escape(d["asset"])}"' if d.get("asset") else ""
    nome = escape(d["name"])
    if d["type"] == "dataObjectReference":
        return [
            f'    <bpmn:dataObject id="{d["id"]}_obj" />',
            f'    <bpmn:dataObjectReference id="{d["id"]}" name="{nome}" dataObjectRef="{d["id"]}_obj"{ativo} />',
        ]
    return [f'    <bpmn:dataStoreReference id="{d["id"]}" name="{nome}"{ativo} />']


def _bounds(caixa) -> str:
    x, y, w, h = caixa
    return f'<dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" />'


def _xml_diagrama(proc: dict, lay: Layout) -> list[str]:
    pid = proc["id"]
    altura_total = sum(a for _, a in lay.raias.values())
    linhas = [
        f'  <bpmndi:BPMNDiagram id="Diagram_{pid}">',
        f'    <bpmndi:BPMNPlane id="Plane_{pid}" bpmnElement="Collab_{pid}">',
        f'      <bpmndi:BPMNShape id="Pool_{pid}_di" bpmnElement="Pool_{pid}" isHorizontal="true">',
        f"        {_bounds((20, Y0, lay.largura, altura_total))}",
        "      </bpmndi:BPMNShape>",
    ]
    for rid, (topo, altura) in lay.raias.items():
        linhas += [
            f'      <bpmndi:BPMNShape id="{rid}_di" bpmnElement="{rid}" isHorizontal="true">',
            f"        {_bounds((50, topo, lay.largura - 30, altura))}",
            "      </bpmndi:BPMNShape>",
        ]
    gateways = {e["id"] for e in proc["elements"] if e["type"] in bpmn.GATEWAYS}
    for eid, caixa in lay.geo.items():
        marcador = ' isMarkerVisible="true"' if eid in gateways else ""
        linhas += [
            f'      <bpmndi:BPMNShape id="{eid}_di" bpmnElement="{eid}"{marcador}>',
            f"        {_bounds(caixa)}",
        ]
        if eid in lay.rotulos:
            linhas.append(f"        <bpmndi:BPMNLabel>{_bounds(lay.rotulos[eid])}</bpmndi:BPMNLabel>")
        linhas.append("      </bpmndi:BPMNShape>")
    associacoes = []
    for e in proc["elements"]:
        _, ty, _, th = lay.geo[e["id"]]
        for i, d in enumerate(e.get("inputs", []), start=1):
            dx, dy, dw, _ = lay.geo[d]
            associacoes.append((f"DataIn_{e['id']}_{i}", [(dx + dw / 2, dy), (dx + dw / 2, ty + th)]))
        for i, d in enumerate(e.get("outputs", []), start=1):
            dx, dy, dw, _ = lay.geo[d]
            associacoes.append((f"DataOut_{e['id']}_{i}", [(dx + dw / 2, ty + th), (dx + dw / 2, dy)]))
    for fid, pontos in [*lay.rotas.items(), *associacoes]:
        linhas.append(f'      <bpmndi:BPMNEdge id="{fid}_di" bpmnElement="{fid}">')
        linhas += [f'        <di:waypoint x="{x:.0f}" y="{y:.0f}" />' for x, y in pontos]
        if fid in lay.rotulos:
            linhas.append(f"        <bpmndi:BPMNLabel>{_bounds(lay.rotulos[fid])}</bpmndi:BPMNLabel>")
        linhas.append("      </bpmndi:BPMNEdge>")
    return [*linhas, "    </bpmndi:BPMNPlane>", "  </bpmndi:BPMNDiagram>"]


def gerar_xml(proc: dict, origem: str) -> str:
    erros = validar_estrutura(proc)
    if erros:
        raise ValueError(f"{origem}: processo inválido:\n  - " + "\n  - ".join(erros))
    pid, fluxos, lay = proc["id"], _fluxos(proc), calcular_layout(proc)
    linhas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        gerado.cabecalho(
            "statera bpmn",
            f"{origem} (o processo em YAML, espelho do BPMN)",
            estilo="xml",
            nota="Para desenhar: abra este .bpmn no Camunda Modeler ou no bpmn.io e rode\n"
            "`statera bpmn import <este arquivo>` para levar a mudança ao YAML.",
        ).rstrip("\n"),
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
        'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
        f'xmlns:xsi="{bpmn.NS["xsi"]}" xmlns:statera="{bpmn.NS["statera"]}" id="Definitions_{pid}" '
        'targetNamespace="urn:statera:processos" exporter="statera (statera bpmn)" exporterVersion="1.0">',
        f'  <bpmn:collaboration id="Collab_{pid}">',
        f'    <bpmn:participant id="Pool_{pid}" name="{escape(proc["name"])}" processRef="{pid}" />',
        "  </bpmn:collaboration>",
        f'  <bpmn:process id="{pid}" isExecutable="false">',
    ]
    if proc.get("description"):
        linhas.append(f"    <bpmn:documentation>{escape(proc['description'])}</bpmn:documentation>")
    linhas.append(f'    <bpmn:laneSet id="LaneSet_{pid}">')
    for r in proc["lanes"]:
        linhas.append(f'      <bpmn:lane id="{r["id"]}" name="{escape(r["name"])}" statera:papel="{r["role"]}">')
        linhas += [
            f"        <bpmn:flowNodeRef>{e['id']}</bpmn:flowNodeRef>" for e in proc["elements"] if e["lane"] == r["id"]
        ]
        linhas.append("      </bpmn:lane>")
    linhas.append("    </bpmn:laneSet>")
    for e in proc["elements"]:
        linhas += _xml_elemento(e, fluxos)
    for f in fluxos:
        nome = f' name="{escape(f["name"])}"' if f.get("name") else ""
        abertura = f'    <bpmn:sequenceFlow id="{f["id"]}" sourceRef="{f["from"]}" targetRef="{f["to"]}"{nome}'
        if not f.get("condition"):
            linhas.append(abertura + " />")
            continue
        linhas += [
            abertura + ">",
            '      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
            f"${{situacao == '{escape(f['condition'])}'}}</bpmn:conditionExpression>",
            "    </bpmn:sequenceFlow>",
        ]
    for d in proc.get("data", []):
        linhas += _xml_dado(d)
    linhas.append("  </bpmn:process>")
    linhas += _xml_diagrama(proc, lay)
    linhas.append("</bpmn:definitions>")
    return "\n".join(linhas) + "\n"


def destino_bpmn(fonte: Path) -> Path:
    return fonte.with_suffix(".bpmn")


def sincronizar() -> list[Path]:
    gerados = []
    for fonte in fontes():
        destino = destino_bpmn(fonte)
        destino.write_text(gerar_xml(carregar(fonte), fonte.name), encoding="utf-8")
        gerados.append(destino)
    return gerados


def desatualizados() -> list[Path]:
    """Fontes YAML cujo .bpmn não existe ou difere do que seria gerado."""
    saida = []
    for fonte in fontes():
        destino = destino_bpmn(fonte)
        esperado = gerar_xml(carregar(fonte), fonte.name)
        if not destino.exists() or destino.read_text(encoding="utf-8") != esperado:
            saida.append(fonte)
    return saida


def bpmn_sem_fonte() -> list[Path]:
    projeto = atual()
    pastas = {u.fase(2) / "processes" for u in projeto.unidades} | {projeto.governanca / "processes"}
    return [b for pasta in sorted(pastas) for b in sorted(pasta.glob("*.bpmn")) if not b.with_suffix(".yaml").exists()]


# ---------------------------------------------------------------------------
# Importação: .bpmn (editado em ferramenta visual) -> YAML
# ---------------------------------------------------------------------------


def _ext_de_attrs(attrs: dict) -> dict:
    ext = {}
    for chave in ATRIBUTOS:
        if chave not in attrs:
            continue
        valor = attrs[chave]
        if chave == "durationMin":
            valor = float(valor) if "." in valor else int(valor)
        elif chave == "models":
            valor = [m for m in valor.split(",") if m]
        elif chave == "exception":
            valor = valor == "true"
        ext[chave] = valor
    return ext


def para_dict(caminho: Path) -> dict:
    p = bpmn.ler(caminho)
    raiz = ET.parse(caminho).getroot()
    processo = raiz.find("bpmn:process", bpmn.NS)
    proc: dict = {"id": processo.get("id"), "name": p.nome}
    doc = processo.find("bpmn:documentation", bpmn.NS)
    if doc is not None and doc.text:
        proc["description"] = doc.text.strip()
    proc["lanes"] = [{"id": lid, "name": r["name"], "role": r["role"]} for lid, r in p.lanes.items()]
    elementos, dados = [], []
    for el in p.elementos.values():
        if el.tag in ("dataStoreReference", "dataObjectReference"):
            dados.append(
                {"id": el.id, "type": el.tag, "name": el.nome}
                | ({"asset": el.attrs["asset"]} if "asset" in el.attrs else {})
            )
            continue
        item: dict = {"id": el.id, "type": el.tag, "name": el.nome, "lane": el.lane}
        if el.timer:
            item["event"] = "timer"
        elif el.mensagem:
            item["event"] = "message"
        if el.documentacao:
            item["documentation"] = el.documentacao
        entradas = [f["from"] for f in p.fluxos.values() if f["tag"] == "dataInput" and f["to"] == el.id]
        saidas = [f["to"] for f in p.fluxos.values() if f["tag"] == "dataOutput" and f["from"] == el.id]
        if entradas:
            item["inputs"] = entradas
        if saidas:
            item["outputs"] = saidas
        if ext := _ext_de_attrs(el.attrs):
            item["statera"] = ext
        elementos.append(item)
    proc["elements"] = elementos
    if dados:
        proc["data"] = dados
    proc["flows"] = [
        {"id": fid, "from": f["from"], "to": f["to"]}
        | ({"name": f["name"]} if f["name"] else {})
        | ({"condition": f["condition"]} if f.get("condition") else {})
        for fid, f in p.fluxos.items()
        if f["tag"] == "sequenceFlow"
    ]
    return proc


def _em_linha(valor) -> str:
    texto = yaml.safe_dump(valor, default_flow_style=True, allow_unicode=True, sort_keys=False, width=10_000)
    return texto.strip().removesuffix("...").strip()  # escalares saem com marcador de fim de documento


LARGURA_EM_LINHA = 100


def _valor_yaml(chave: str, valor, recuo: str) -> list[str]:
    """`chave: valor` em linha quando cabe; senão em bloco (listas e mapeamentos longos)."""
    em_linha = _em_linha(valor)
    if not isinstance(valor, list | dict) or len(recuo) + len(chave) + len(em_linha) + 2 <= LARGURA_EM_LINHA:
        return [f"{recuo}{chave}: {em_linha}"]
    linhas = [f"{recuo}{chave}:"]
    if isinstance(valor, list):
        return linhas + [f"{recuo}  - {_em_linha(item)}" for item in valor]
    for k, v in valor.items():
        linhas += _valor_yaml(k, v, recuo + "  ")
    return linhas


def _elemento_yaml(e: dict) -> list[str]:
    em_linha = _em_linha(e)
    if len(em_linha) + 4 <= LARGURA_EM_LINHA:
        return [f"  - {em_linha}"]
    linhas = []
    for i, (k, v) in enumerate(e.items()):
        bloco = _valor_yaml(k, v, "    ")
        if i == 0:
            bloco[0] = "  - " + bloco[0].lstrip()
        linhas += bloco
    return linhas


def para_yaml(proc: dict, origem: str = "") -> str:
    cabecalho = [
        "# Processo em YAML, espelho 1:1 do BPMN 2.0 (schema: statera process.schema.json).",
        "# Fonte do .bpmn de mesmo nome, gerado por `statera bpmn`." + (f" Importado de {origem}." if origem else ""),
    ]
    topo = {k: proc[k] for k in ("id", "name", "description") if k in proc}
    corpo = yaml.safe_dump(topo, allow_unicode=True, sort_keys=False, width=110).rstrip()
    partes = [*cabecalho, corpo, "", "raias:"]
    partes += [f"  - {_em_linha(r)}" for r in proc["lanes"]]
    partes += ["", "elementos:"]
    for e in proc["elements"]:
        partes += _elemento_yaml(e)
    if proc.get("data"):
        partes += ["", "dados:"] + [f"  - {_em_linha(d)}" for d in proc["data"]]
    partes += ["", "fluxos:"] + [f"  - {_em_linha(f)}" for f in proc["flows"]]
    return "\n".join(partes) + "\n"


def importar(caminho: Path, destino: Path | None = None) -> Path:
    destino = destino or caminho.with_suffix(".yaml")
    destino.write_text(para_yaml(para_dict(caminho), caminho.name), encoding="utf-8")
    return destino
