"""Fase 8 — Avaliar. Tarefa do processo: `implementacao: py:statera.evaluate.step`."""

from __future__ import annotations

from statera.edicao import Edicao
from statera.evaluate import avaliacao


def step(ed: Edicao) -> str:
    """GSBPM 8.1 e 8.2 — indicadores de qualidade da edição."""
    relatorio = avaliacao.avaliar(ed)
    ed.log(f"  avaliação: {ed.projeto.rel(relatorio)}")
    return "ok"
