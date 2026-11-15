"""Calendário dos produtos internos: os cortes que os consumidores pedem (especificação 09).

Um produto interno não tem calendário próprio: entrega os cortes que os calendários dos produtos
de divulgação pedem, diretamente ou através de outros produtos internos (eSocial → vínculos de
emprego → Novo CAGED). O arquivo `1-specify/calendar.yaml` de cada produto interno é GERADO
desses calendários, para que a data de corte tenha uma só fonte: o calendário de quem divulga.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from statera import gerado, portas
from statera.projeto import Projeto, Unidade, atual

RELATIVO = "1-specify/calendar.yaml"


def consumidores(projeto: Projeto, unidade: Unidade) -> list[Unidade]:
    return [u for u in projeto.produtos() if unidade.nome in {e.nome for e in portas.produtos_de_entrada(u)}]


def pedidos(projeto: Projeto, unidade: Unidade, _vistos: frozenset = frozenset()) -> dict[str, list[str]]:
    """{data de corte: edições que a pedem}, subindo pelos consumidores até os produtos de divulgação."""
    saida: dict[str, set[str]] = {}
    for c in consumidores(projeto, unidade):
        if c.nome in _vistos:
            continue
        if c.interno:
            for data, quem in pedidos(projeto, c, _vistos | {unidade.nome}).items():
                saida.setdefault(data, set()).update(quem)
        else:
            for e in (c.ler(RELATIVO) or {}).get("editions", []):
                saida.setdefault(str(e["cut_date"]), set()).add(f"{c.nome} {e['period']}")
    return {data: sorted(quem) for data, quem in sorted(saida.items())}


def gerar(projeto: Projeto, unidade: Unidade) -> str:
    conteudo = {
        "product": unidade.nome,
        "cut_date_rule": "Cortes pedidos pelos calendários dos produtos que leem este produto",
        "cuts": [{"cut_date": d, "requested_by": q} for d, q in pedidos(projeto, unidade).items()],
    }
    aviso = gerado.cabecalho(
        "statera sync",
        "os calendários (1-specify/calendar.yaml) dos produtos de divulgação que leem este produto",
        nota="Produto interno: não tem calendário próprio; entrega os cortes que os consumidores pedem.",
    )
    return aviso + yaml.safe_dump(conteudo, allow_unicode=True, sort_keys=False, width=110)


def gerados(projeto: Projeto | None = None) -> dict[Path, str]:
    projeto = projeto or atual()
    return {u.pasta / RELATIVO: gerar(projeto, u) for u in projeto.produtos() if u.interno}


def sincronizar(projeto: Projeto | None = None) -> list[Path]:
    escritos = []
    for caminho, texto in gerados(projeto).items():
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
        escritos.append(caminho)
    return escritos


def desatualizados(projeto: Projeto | None = None) -> list[Path]:
    return [c for c, t in gerados(projeto).items() if not c.exists() or c.read_text(encoding="utf-8") != t]
