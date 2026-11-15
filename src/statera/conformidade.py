"""Níveis de conformidade de um produto (especificação v0, `08-niveis-de-conformidade.md`).

Produto de divulgação (alinhamento consumo):
    1 Catalogado     produto especificado (ODPS), contratos das saídas no perfil statera,
                     classificação aprovada, responsáveis, onde o público o acessa (série ou
                     distribuições declaradas)
    2 Reprodutível   calendário com data de corte, série de publicação, saídas construídas por
                     código versionado (modelos dbt), consultas recorrentes catalogadas
    3 Controlado     regras com casos executáveis (no produto ou nos produtos internos de que ele
                     depende), controles, gate de publicação aberta, processo executável, linhagem
                     até o bronze
    4 Avaliado       indicadores declarados e calculáveis, plano de ação, paridade com o legado

Produto interno (alinhamento fonte ou agregado): no lugar da série e do gate de publicação, exige
consumidores autorizados, origem da extração e retenção (fonte) ou partição por corte (agregado),
e regras com casos (agregado).

Cada produto declara o nível-alvo em `product.odps.yaml` (customProperties.conformanceLevel).
O nível ATINGIDO é o maior N tal que todas as exigências de 1 a N são cumpridas. Os requisitos
são verificados sem dados (rodam em qualquer CI).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from statera import classificacao, contratos, portas
from statera.projeto import Projeto, Unidade, atual

NOMES = {1: "catalogado", 2: "reprodutível", 3: "controlado", 4: "avaliado"}


@dataclass
class Resultado:
    unidade: Unidade
    alvo: int
    atingido: int
    pendencias: list[tuple[int, str]] = field(default_factory=list)


def _alvo(odps: dict) -> int:
    return int(contratos.propriedade_custom(odps, "conformanceLevel", 1))


def _saidas(u: Unidade, ctx: dict) -> list[tuple[dict, dict]]:
    """(porta, contrato) de cada porta de saída com contrato existente."""
    todos = ctx["contracts"]
    return [(p, todos[p["contractId"]][1]) for p in portas.portas_de_saida(u) if p["contractId"] in todos]


# ---------------------------------------------------------------------------
# Comuns
# ---------------------------------------------------------------------------


def _nivel1(u: Unidade, ctx: dict) -> list[str]:
    arquivos = ("product.odps.yaml", "classification.yaml", "owners.yaml")
    pend = [f"falta 1-specify/{a}" for a in arquivos if not (u.fase(1) / a).exists()]
    if pend:
        return pend
    aprovados = {a["contract"]: a for a in (u.ler("1-specify/classification.yaml") or {}).get("assets", [])}
    for p in portas.portas_de_saida(u):
        if p["contractId"] not in ctx["contracts"]:
            pend.append(f"saída {p['name']}: contrato {p['contractId']} não existe")
    for _, c in _saidas(u, ctx):
        if not contratos.propriedade_custom(c, "urn"):
            pend.append(f"{c['id']}: sem urn (espaço de nomes)")
        pend += [f"{c['id']}: {e}" for e in classificacao.coerencia(c)]
        pend += [f"{c['id']}: {e}" for e in classificacao.aprovacao_confere(c, aprovados.get(c["id"]))]
    if u.interno and not portas.consumidores_autorizados(u):
        pend.append("produto interno sem authorizedConsumers (quem pode ler e com que finalidade)")
    if not u.interno:
        from statera import acesso

        pend += acesso.problemas_distribuicoes(u)
    return pend


def _falta(u: Unidade, relativo: str, rotulo: str) -> list[str]:
    """Pendência de um arquivo do esqueleto: ausente, ou presente declarando que não se aplica."""
    if u.tem(relativo):
        return []
    if (motivo := u.nao_se_aplica(relativo)) is not None:
        return [f"{relativo} declara que não se aplica ({motivo}); o nível pede {rotulo}"]
    return [f"falta {relativo} ({rotulo})"]


def _processo_executavel(u: Unidade) -> list[str]:
    if falta := _falta(u, "2-design/processes/to-be.yaml", "processo executável"):
        return falta
    if not u.processo_to_be.with_suffix(".bpmn").exists():
        return ["to-be.bpmn não gerado (statera bpmn)"]
    return []


def _tem_regras(u: Unidade, ctx: dict) -> bool:
    return any(r.unidade and r.unidade.nome in ctx["cadeia"][u.nome] for r in ctx["rules"])


# ---------------------------------------------------------------------------
# Produto de divulgação
# ---------------------------------------------------------------------------


def _consumo2(u: Unidade, ctx: dict) -> list[str]:
    pend = []
    pend += _falta(u, "1-specify/calendar.yaml", "datas de corte")
    pend += _falta(u, "7-disseminate/series.yaml", "série de publicação")
    for p, c in _saidas(u, ctx):
        no = contratos.no_dbt(c)
        if not no or no[1] not in ctx["models"]:
            pend.append(f"saída {p['name']}: não é construída por um modelo dbt do repositório")
    if not any(c.meta.get("product") == u.nome for c in ctx["queries"]):
        pend.append("nenhuma consulta recorrente catalogada para o produto")
    return pend


def _consumo3(u: Unidade, ctx: dict) -> list[str]:
    pend = []
    if not _tem_regras(u, ctx):
        pend.append("nenhuma regra com casos executáveis no produto ou nos produtos internos de que depende")
    if not u.tem("6-analyse/controls.yaml") and not any((u.fase(6) / "dbt-tests").glob("*.sql")):
        pend.append("sem controles (6-analyse/controls.yaml ou dbt-tests/)")
    for p, c in _saidas(u, ctx):
        controle = classificacao.controle_exigido(c) or {}
        nao_publicadas = controle.get("unpublishedColumns", [])
        publicadas = [x["name"] for x in c["schema"][0]["properties"] if x["name"] not in nao_publicadas]
        motivos = classificacao.impedimentos_publicacao(c, publicadas, controle_aplicado=bool(controle))
        pend += [f"saída {p['name']} não passaria no gate de publicação: {m}" for m in motivos]
    pend += _processo_executavel(u)
    pend += [f"saída {m}: linhagem não chega a uma fonte bronze" for m in ctx["sem_bronze"].get(u.nome, [])]
    return pend


def _consumo4(u: Unidade, ctx: dict) -> list[str]:
    from statera.evaluate import avaliacao

    if falta := _falta(u, "8-evaluate/indicators.yaml", "indicadores de qualidade"):
        return falta
    pend = list(avaliacao.problemas_de_declaracao(u))
    if not avaliacao.indicadores(u).get("action_plan"):
        pend.append("indicators.yaml sem plano_de_acao (GSBPM 8.3)")
    paridade = contratos.propriedade_custom(portas.odps(u), "legacyParity", "not-declared")
    if paridade not in ("done", "not-applicable"):
        pend.append(f"paridade com o legado: {paridade}")
    return pend


# ---------------------------------------------------------------------------
# Produto interno (fonte ou agregado)
# ---------------------------------------------------------------------------


def _interno2(u: Unidade, ctx: dict) -> list[str]:
    pend = []
    for p, c in _saidas(u, ctx):
        if u.alinhamento == "source":
            if not contratos.propriedade_custom(c, "extraction"):
                pend.append(f"saída {p['name']}: contrato sem customProperties.extraction")
            if not any(s.get("property") == "retention" for s in c.get("slaProperties", []) or []):
                pend.append(f"saída {p['name']}: contrato sem retenção declarada (slaProperties.retention)")
            continue
        no = contratos.no_dbt(c)
        if not no or no[1] not in ctx["models"]:
            pend.append(f"saída {p['name']}: não é construída por um modelo dbt do repositório")
        if not contratos.propriedade_custom(c, "partitionedBy"):
            pend.append(f"saída {p['name']}: não guarda os cortes (customProperties.partitionedBy)")
    return pend


def _interno3(u: Unidade, ctx: dict) -> list[str]:
    pend = _processo_executavel(u)
    for p, c in _saidas(u, ctx):
        if not c["schema"][0].get("quality"):
            pend.append(f"saída {p['name']}: contrato sem regras de qualidade")
    if u.alinhamento == "aggregate":
        if not _tem_regras(u, ctx):
            pend.append("nenhuma regra de negócio com casos executáveis")
        pend += [f"saída {m}: linhagem não chega a uma fonte bronze" for m in ctx["sem_bronze"].get(u.nome, [])]
    return pend


def _interno4(u: Unidade, ctx: dict) -> list[str]:
    return _falta(u, "8-evaluate/indicators.yaml", "indicadores de serviço: atraso, completude")


REQUISITOS: dict[int, Callable[[Unidade, dict], list[str]]] = {1: _nivel1, 2: _consumo2, 3: _consumo3, 4: _consumo4}
REQUISITOS_INTERNO: dict[int, Callable[[Unidade, dict], list[str]]] = {
    1: _nivel1, 2: _interno2, 3: _interno3, 4: _interno4,
}  # fmt: skip


# ---------------------------------------------------------------------------
# Contexto (calculado uma vez) e avaliação
# ---------------------------------------------------------------------------


def _cadeia(u: Unidade) -> set[str]:
    nomes = {u.nome}
    for entrada in portas.produtos_de_entrada(u):
        nomes |= _cadeia(entrada)
    return nomes


def _contexto(projeto: Projeto) -> dict:
    from statera.consume import queries
    from statera.process import dbt, regras

    manifesto = dbt.manifesto(gerar=False, projeto=projeto)
    nos = manifesto["nodes"]
    modelos = {n["name"]: uid for uid, n in nos.items() if n["resource_type"] in ("model", "seed")}

    def chega_ao_bronze(uid: str, vistos: set) -> bool:
        if uid.startswith("source."):
            return True
        if uid in vistos or uid not in nos:
            return False
        vistos.add(uid)
        return any(chega_ao_bronze(p, vistos) for p in nos[uid]["depends_on"]["nodes"])

    todos = contratos.todos(projeto)
    sem_bronze = {}
    for u in projeto.produtos():
        faltando = []
        for p in portas.portas_de_saida(u):
            c = todos.get(p["contractId"], (None, {}))[1]
            no = contratos.no_dbt(c) if c else None
            if no and no[0] == "model" and no[1] in modelos and not chega_ao_bronze(modelos[no[1]], set()):
                faltando.append(p["name"])
        sem_bronze[u.nome] = faltando
    return {
        "contracts": todos,
        "models": set(modelos),
        "queries": queries.load(projeto),
        "rules": regras.carregar(projeto),
        "sem_bronze": sem_bronze,
        "cadeia": {u.nome: _cadeia(u) for u in projeto.produtos()},
    }


def avaliar(projeto: Projeto | None = None, nivel_forcado: int | None = None) -> list[Resultado]:
    projeto = projeto or atual()
    ctx = _contexto(projeto)
    resultados = []
    for u in projeto.produtos():
        requisitos = REQUISITOS_INTERNO if u.interno else REQUISITOS
        alvo = nivel_forcado or _alvo(portas.odps(u))
        atingido, pendencias = 0, []
        for nivel in sorted(requisitos):
            faltas = requisitos[nivel](u, ctx)
            pendencias += [(nivel, f) for f in faltas]
            if faltas:
                break
            atingido = nivel
        resultados.append(Resultado(u, alvo, atingido, pendencias))
    return resultados
