"""Implementações falsas de tarefas, usadas pelo processo do projeto de teste."""

import os

CHAMADAS: list[tuple[str, str]] = []


def registrar(ed) -> str:
    CHAMADAS.append(("A", ed.competencia))
    return "ok"


def decidir(ed) -> str:
    return os.environ.get("ETAPA_DECISAO", "ok")


def publicar(ed) -> str:
    CHAMADAS.append(("P", ed.competencia))
    return "ok"


def extrair(ed) -> str:
    CHAMADAS.append(("E", ed.data_corte))
    return "ok"


def marcar_corte(ed) -> str:
    """Versão mínima de statera.disseminate.mark_cut: grava o manifesto imutável do corte."""
    import json

    from statera.collect import arquivo_corte

    destino = arquivo_corte(ed.unidade, ed.data_corte)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"product": ed.produto, "cutDate": ed.data_corte, "run": ed.run_id}))
    return "ok"
