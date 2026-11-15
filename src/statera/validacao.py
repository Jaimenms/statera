"""Validação estática de um repositório de conteúdo (roda no CI a cada pull request, sem dados).

Grupos (e o nível de conformidade a partir do qual cada um é exigido):

  esquemas       (1) YAML/JSON contra os esquemas oficiais (ODCS, ODPS, Data Package, OpenLineage)
                     e os da especificação statera
  perfil         (1) contratos no perfil statera: camada coerente com a pasta, urn no espaço de
                     nomes, classificação em quatro dimensões coerente com as colunas
  referencias    (1) contratos, portas, aprovações, papéis, séries, regras e indicadores apontam
                     para coisas que existem
  camadas        (2) dependências permitidas do medalhão no grafo do dbt
  consumo        (2) consultas (metadados, parâmetros, testes), métricas e receitas
  classificacao  (3) consumidores amplos só leem ativos publicáveis; consultas que leem dado
                     restrito e saem amplas declaram controle de divulgação
  processos      (3) processos válidos e executáveis: gateways com condições, implementações e
                     papéis existentes
  gerados        (1-3) arquivos gerados em dia (CODEOWNERS, projeto dbt, unit tests, BPMN)
  higiene        (1) nenhum dado nem segredo fora das pastas data/
  conformidade   (alvo) cada produto atinge o nível que declara (ou o pedido em --level)

`statera validate --level N` verifica só o que o nível N exige.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, Draft201909Validator, Draft202012Validator

from statera import classificacao, contratos, governanca, portas, referencias
from statera.design import bpmn, processos
from statera.disseminate import registro
from statera.edicao import ler_jsonl
from statera.process import dbt, regras
from statera.projeto import ALINHAMENTOS, ESQUEMAS, FASES, VISIBILIDADES, Projeto, atual, ler_yaml


@dataclass
class Achado:
    grupo: str
    ok: bool
    mensagem: str
    nivel: int = 1


VALIDADORES = {
    "odcs": ("standards/odcs-json-schema-v3.1.0.json", Draft201909Validator),
    "odps": ("standards/odps-json-schema-v1.0.0.json", Draft201909Validator),
    "datapackage": ("standards/datapackage-2.0.json", Draft7Validator),
    "openlineage": ("standards/openlineage-2-0-2.json", Draft202012Validator),
    **{
        nome: (f"statera/{nome}.schema.json", Draft202012Validator)
        for nome in (
            "owners",
            "roles",
            "classification",
            "classification-asset",
            "calendar",
            "rule-cases",
            "process",
            "indicators",
            "controls",
            "query",
            "query-test",
            "metrics",
        )
    },
    "series": ("statera/publication-series.schema.json", Draft202012Validator),
}


@cache
def _validador(nome: str):
    caminho, classe = VALIDADORES[nome]
    return classe(json.loads((ESQUEMAS / caminho).read_text(encoding="utf-8")))


def _ler(caminho: Path):
    texto = caminho.read_text(encoding="utf-8")
    return json.loads(texto) if caminho.suffix == ".json" else yaml.safe_load(texto)


def _alvos(projeto: Projeto) -> list[tuple[Path, str]]:
    alvos = [
        (projeto.governanca / "roles.yaml", "roles"),
        (projeto.governanca / "shared-owners.yaml", "owners"),
    ]
    por_arquivo = {
        "1-specify/product.odps.yaml": "odps",
        "1-specify/classification.yaml": "classification",
        "1-specify/owners.yaml": "owners",
        "1-specify/calendar.yaml": "calendar",
        "5-process/metrics.yaml": "metrics",
        "6-analyse/controls.yaml": "controls",
        "7-disseminate/series.yaml": "series",
        "8-evaluate/indicators.yaml": "indicators",
    }
    for u in projeto.unidades:
        alvos += [(u.pasta / rel, esquema) for rel, esquema in por_arquivo.items() if u.tem(rel)]
        alvos += [(c, "odcs") for c in u.contratos()]
        alvos += [(c, "process") for c in u.processos()]
    alvos += [(c, "rule-cases") for pasta in projeto.pastas_regras() for c in sorted(pasta.glob("*/cases.yaml"))]
    for base in projeto.pastas_consultas():
        if base.is_dir():
            alvos += [(c, "query") for c in sorted(base.rglob("query.yaml"))]
            alvos += [(c, "query-test") for c in sorted(base.rglob("test.yaml"))]
    return alvos


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------


def verificar_esquemas(projeto: Projeto) -> list[Achado]:
    achados = []
    for caminho, esquema in _alvos(projeto):
        try:
            erros = list(_validador(esquema).iter_errors(_ler(caminho)))
        except (yaml.YAMLError, json.JSONDecodeError) as erro:
            achados.append(Achado("esquemas", False, f"{projeto.rel(caminho)}: sintaxe inválida ({erro})"))
            continue
        for e in erros[:5]:
            local = "/".join(map(str, e.absolute_path)) or "(raiz)"
            achados.append(Achado("esquemas", False, f"{projeto.rel(caminho)} [{esquema}] {local}: {e.message[:200]}"))
        if not erros:
            achados.append(Achado("esquemas", True, f"{projeto.rel(caminho)} conforme {esquema}"))
    for u in projeto.produtos():
        eventos = ler_jsonl(u.pasta_execucoes / "eventos.jsonl")
        if eventos:
            invalidos = sum(1 for ev in eventos if list(_validador("openlineage").iter_errors(ev)))
            achados.append(
                Achado(
                    "esquemas", invalidos == 0, f"{u.nome}: {len(eventos)} eventos OpenLineage, {invalidos} inválidos"
                )
            )
    return achados


# ---------------------------------------------------------------------------
# Perfil statera dos contratos
# ---------------------------------------------------------------------------

CAMADA_ESPERADA = {("source", 4): "bronze", ("aggregate", 5): "silver", ("consumer", 5): "gold"}


def _camada_esperada(projeto: Projeto, caminho: Path) -> str | None:
    unidade = projeto.unidade_de(caminho)
    if unidade is None:
        return None
    if unidade.tipo == "reference":
        return "ref"
    rel = caminho.relative_to(unidade.pasta).parts[0]
    fase = next((n for n, nome in FASES.items() if nome == rel), None)
    return CAMADA_ESPERADA.get((unidade.alinhamento, fase))


def verificar_perfil(projeto: Projeto) -> list[Achado]:
    achados = []
    for cid, (caminho, c) in sorted(contratos.todos(projeto).items()):
        rel, erros = projeto.rel(caminho), []
        camada, esperada = contratos.camada(c), _camada_esperada(projeto, caminho)
        if camada not in contratos.CAMADAS:
            erros.append(f"camada '{camada}' inválida")
        elif esperada and camada != esperada:
            erros.append(f"camada '{camada}' fora do lugar: nesta pasta, contratos são '{esperada}'")
        unidade = projeto.unidade_de(caminho)
        if unidade and camada in contratos.CAMADAS:
            urn, esperado = contratos.propriedade_custom(c, "urn"), contratos.urn_esperado(unidade, c)
            if urn != esperado:
                erros.append(f"urn '{urn}' deveria ser '{esperado}'")
        cls = classificacao.da_tabela(c)
        erros += [f"classificacao: {e.message}" for e in _validador("classification-asset").iter_errors(cls)]
        if not erros:
            erros += classificacao.coerencia(c)
        if not contratos.no_dbt(c) and not contratos.propriedade_custom(c, "manualProduction"):
            erros.append("sem dbtSource, dbtModel nem dbtSeed (ou manualProduction, para o nível 1)")
        if camada == "ref":
            erros += referencias.problemas(c)
        if camada == "bronze" and not contratos.propriedade_custom(c, "extraction"):
            erros.append("contrato do bronze sem customProperties.extraction (origem da extração)")
        achados += [Achado("profile", False, f"{rel}: {e}") for e in erros]
        if not erros:
            achados.append(Achado("profile", True, f"{cid}: perfil statera ({camada}, {classificacao.resumo(cls)})"))
    return achados


# ---------------------------------------------------------------------------
# Referências cruzadas
# ---------------------------------------------------------------------------


def _nos(manifesto: dict) -> tuple[dict, set]:
    modelos = {n["name"]: n for n in manifesto["nodes"].values() if n["resource_type"] in ("model", "seed")}
    sources = {f"{s['source_name']}.{s['name']}" for s in manifesto["sources"].values()}
    return modelos, sources


def _ref_contratos(projeto: Projeto, manifesto: dict, todos: dict) -> list[Achado]:
    achados = []
    modelos, sources = _nos(manifesto)
    for caminho, c in todos.values():
        tipo, nome = contratos.no_dbt(c) or (None, None)
        if (tipo == "source" and nome not in sources) or (tipo in ("model", "seed") and nome not in modelos):
            achados.append(
                Achado("references", False, f"{projeto.rel(caminho)}: dbt{tipo.title()} '{nome}' não existe")
            )
    contratados = {(contratos.no_dbt(c) or (None, None))[1] for _, c in todos.values()}
    for nome, n in modelos.items():
        meta = (n.get("config") or {}).get("meta") or {}
        if meta.get("layer") == "gold" and nome not in contratados:
            achados.append(Achado("references", False, f"modelo gold '{nome}' sem contrato ODCS"))
    achados.append(Achado("references", True, f"{len(todos)} contratos ligados a nós do dbt"))
    return achados


def _ref_unidades(projeto: Projeto, todos: dict) -> list[Achado]:
    achados = []
    nomes = [u.nome for u in projeto.unidades]
    if repetidos := sorted({n for n in nomes if nomes.count(n) > 1}):
        achados.append(
            Achado("references", False, f"nomes de unidade repetidos (colidem no espaço de nomes): {repetidos}")
        )
    for u in projeto.produtos():
        aprovacao = u.ler("1-specify/classification.yaml") or {}
        aprovados = {a["contract"]: a for a in aprovacao.get("assets", [])}
        for caminho in u.contratos():
            c = ler_yaml(caminho)
            erros = classificacao.aprovacao_confere(c, aprovados.get(c["id"]))
            achados += [Achado("references", False, f"{c['id']}: {e}") for e in erros]
        for cid in sorted(set(aprovados) - {ler_yaml(c)["id"] for c in u.contratos()}):
            achados.append(
                Achado(
                    "references", False, f"{u.relativo}: classification.yaml aprova '{cid}', que não é desta unidade"
                )
            )
        urn = None
        if u.alinhamento not in ALINHAMENTOS or u.propriedade("visibility", "public") not in VISIBILIDADES:
            achados.append(
                Achado(
                    "references", False, f"{u.nome}: alinhamento/visibilidade fora de {ALINHAMENTOS}/{VISIBILIDADES}"
                )
            )
        if u.interno and u.alinhamento == "consumer":
            achados.append(
                Achado("references", False, f"{u.nome}: produto de consumo (divulgação) não pode ser interno")
            )
        if u.tipo == "product":
            odps = u.ler("1-specify/product.odps.yaml")
            urn = contratos.propriedade_custom(odps, "urn")
            portas_ = [p["contractId"] for p in [*odps.get("inputPorts", []), *odps.get("outputPorts", [])]]
            achados += [
                Achado("references", False, f"{u.nome}: porta aponta para contrato inexistente '{p}'")
                for p in portas_
                if p not in todos
            ]
            for p in odps.get("outputPorts", []):
                dono = projeto.unidade_de(todos[p["contractId"]][0]) if p["contractId"] in todos else None
                if dono and dono.nome != u.nome:
                    achados.append(
                        Achado(
                            "references",
                            False,
                            f"{u.nome}: saída '{p['name']}' é contrato de outra unidade ({dono.nome})",
                        )
                    )
        if urn != u.urn:
            achados.append(Achado("references", False, f"{u.relativo}: urn '{urn}' deveria ser '{u.urn}'"))
    achados.append(Achado("references", True, f"{len(projeto.unidades)} unidades: portas, aprovações e urn coerentes"))
    return achados


def _problemas_papeis(projeto: Projeto, u) -> list[str]:
    """Autorização de equipe: papel existente, finalidade, ato e prazo de validade não vencido."""
    from datetime import date

    papeis_ = governanca.papeis(projeto)
    problemas = []
    for a in portas.papeis_autorizados(u):
        rotulo = f"autorização do papel '{a['role']}'"
        if a["role"] not in papeis_:
            problemas.append(f"{rotulo}: papel não existe em governance/roles.yaml")
        problemas += [f"{rotulo}: falta '{c}'" for c in ("purpose", "until", "act") if not a.get(c)]
        if a.get("until"):
            try:
                ate = date.fromisoformat(str(a["until"]))
            except ValueError:
                problemas.append(f"{rotulo}: 'until' deve ser uma data AAAA-MM-DD")
                continue
            if ate < date.today():
                problemas.append(f"{rotulo}: venceu em {ate}; renove (novo ato) ou retire a autorização")
    return problemas


def _ref_consumo_interno(projeto: Projeto) -> list[Achado]:
    """Quem lê um produto interno precisa estar entre os consumidores autorizados dele (finalidade)."""
    achados, pares = [], 0
    for u in projeto.produtos():
        for entrada in portas.produtos_de_entrada(u):
            pares += 1
            if u.nome not in portas.consumidores_autorizados(entrada):
                achados.append(
                    Achado(
                        "references",
                        False,
                        f"{u.nome} lê o produto interno {entrada.nome} sem estar em authorizedConsumers",
                    )
                )
        if u.interno and not portas.consumidores_autorizados(u):
            achados.append(Achado("references", False, f"{u.nome}: produto interno sem authorizedConsumers"))
        achados += [Achado("references", False, f"{u.nome}: {p}") for p in _problemas_papeis(projeto, u)]
    from statera.consume import queries

    for c in queries.load(projeto):
        for entrada in portas.produtos_lidos_pela_consulta(projeto, c.meta.get("bases", [])):
            pares += 1
            if c.id not in portas.consumidores_autorizados(entrada):
                achados.append(
                    Achado(
                        "references",
                        False,
                        f"consulta {c.id} lê o produto interno {entrada.nome} sem estar em authorizedConsumers",
                    )
                )
    if not any(not a.ok for a in achados):
        achados.append(Achado("references", True, f"{pares} leituras de produtos internos, todas autorizadas"))
    return achados


def _ref_regras(projeto: Projeto, manifesto: dict) -> list[Achado]:
    achados = []
    modelos, _ = _nos(manifesto)
    ids = set()
    for r in regras.carregar(projeto):
        ids.add(r.id)
        if not r.pasta.name.startswith(r.id):
            achados.append(
                Achado("references", False, f"{projeto.rel(r.pasta)}: id '{r.id}' não confere com a pasta", 3)
            )
        if r.modelo not in modelos:
            achados.append(Achado("references", False, f"{r.id}: modelo '{r.modelo}' não existe no dbt", 3))
    citadas = set()
    for n in manifesto["nodes"].values():
        meta = ((n.get("config") or {}).get("meta") or {}) | (n.get("meta") or {})
        citadas.update(meta.get("rules", []))
    achados += [
        Achado("references", False, f"regra {rid} citada no dbt mas sem pasta de regra", 3)
        for rid in sorted(citadas - ids)
    ]
    achados += [
        Achado("references", False, f"regra {rid} não é citada por nenhum nó do dbt", 3)
        for rid in sorted(ids - citadas)
    ]
    achados.append(Achado("references", True, f"{len(ids)} regras com casos e modelo existente", 3))
    return achados


def verificar_regras_python(projeto: Projeto) -> list[Achado]:
    """Casos das regras implementadas em modelos Python (o dbt só faz unit test de SQL)."""
    resultados = regras.testar_casos_python(projeto)
    achados = [
        Achado("rules", False, f"{r.regra} / {r.caso}: {r.mensagem}", 3) for r in resultados if r.situacao == "failed"
    ]
    pulados = [r for r in resultados if r.situacao == "not-run"]
    if pulados:
        regras_puladas = sorted({r.regra for r in pulados})
        achados.append(Achado("rules", True, f"casos não executados aqui ({pulados[0].mensagem}): "
                                              f"{', '.join(regras_puladas)}; o CI os executa", 3))  # fmt: skip
    if not any(not a.ok for a in achados):
        executados = len(resultados) - len(pulados)
        achados.append(Achado("rules", True, f"{executados} caso(s) de regras em modelos Python passaram", 3))
    return achados


def _ref_papeis(projeto: Projeto) -> list[Achado]:
    conhecidos = set(governanca.papeis(projeto))
    achados = [
        Achado("references", False, f"{projeto.rel(caminho)}: papel '{papel}' não existe em governance/roles.yaml")
        for caminho, dados in governanca.responsaveis(projeto)
        for papel in sorted(governanca.papeis_referenciados(dados) - conhecidos)
    ]
    achados.append(Achado("references", True, "papéis dos responsáveis existem no catálogo de papéis"))
    return achados


def _ref_series_indicadores(projeto: Projeto) -> list[Achado]:
    from statera.evaluate import avaliacao

    achados = []
    papeis = set(governanca.papeis(projeto))
    for u, s in registro.series(projeto):
        nomes_portas = {p["name"] for p in u.ler("1-specify/product.odps.yaml").get("outputPorts", [])}
        if s["product"] != u.nome:
            achados.append(
                Achado("references", False, f"{u.nome}: series.yaml aponta para o produto '{s['product']}'", 2)
            )
        if s.get("main_resource") not in nomes_portas:
            achados.append(
                Achado(
                    "references",
                    False,
                    f"{u.nome}: recurso_principal '{s.get('main_resource')}' não é porta de saída",
                    2,
                )
            )
        if not (u.fase(7) / "summary.md.j2").exists():
            achados.append(Achado("references", False, f"{u.nome}: falta 7-disseminate/summary.md.j2", 2))
        for c in (u.ler("6-analyse/controls.yaml") or {}).get("controls", []):
            if c["port"] not in nomes_portas:
                achados.append(Achado("references", False, f"{u.nome} {c['id']}: porta '{c['port']}' não existe", 3))
        if u.tem("8-evaluate/indicators.yaml"):
            achados += [Achado("references", False, f"{u.nome}: {e}", 4) for e in avaliacao.problemas_de_declaracao(u)]
            for acao in avaliacao.indicadores(u).get("action_plan", []):
                if acao["owner"] not in papeis:
                    achados.append(
                        Achado("references", False, f"{u.nome} {acao['id']}: papel '{acao['owner']}' não existe", 4)
                    )
    achados.append(Achado("references", True, "séries, controles e indicadores ligados às portas e papéis", 2))
    return achados


# ---------------------------------------------------------------------------
# Camadas (medalhão)
# ---------------------------------------------------------------------------

PERMITIDO = {  # pasta do modelo -> tipos de pais permitidos
    "staging": {"source"},
    "intermediate": {"staging", "intermediate", "seed"},
    "marts": {"intermediate", "marts", "seed"},
    "analysis": {"intermediate", "marts", "seed"},
}


def _tipo_no(uid: str, manifesto: dict) -> str:
    if uid.startswith("source."):
        return "source"
    n = manifesto["nodes"][uid]
    if n["resource_type"] in ("seed", "analysis"):
        return n["resource_type"]
    return n["fqn"][1] if len(n["fqn"]) > 2 else "model"


def verificar_camadas(manifesto: dict) -> list[Achado]:
    achados = []
    for uid, n in manifesto["nodes"].items():
        if n["resource_type"] not in ("model", "analysis"):
            continue
        pasta = _tipo_no(uid, manifesto)
        for pai in n["depends_on"]["nodes"]:
            tipo_pai = _tipo_no(pai, manifesto)
            if tipo_pai not in PERMITIDO.get(pasta, {tipo_pai}):
                achados.append(
                    Achado("camadas", False, f"{n['name']} ({pasta}) lê {tipo_pai} '{pai.split('.')[-1]}': proibido", 2)
                )
    for e in manifesto["exposures"].values():
        for pai in e["depends_on"]["nodes"]:
            if _tipo_no(pai, manifesto) != "marts":
                achados.append(Achado("camadas", False, f"consumidor '{e['name']}' lê fora da camada gold: {pai}", 2))
    if not achados:
        achados.append(
            Achado(
                "camadas",
                True,
                "staging só lê bronze; intermediate não lê gold; consumidores e consultas não leem bronze nem staging",
                2,
            )
        )
    return achados


# ---------------------------------------------------------------------------
# Classificação (gate estático)
# ---------------------------------------------------------------------------


def verificar_classificacao(projeto: Projeto, manifesto: dict) -> list[Achado]:
    from statera.consume import queries

    achados = []
    por_tabela = contratos.por_tabela(projeto)
    por_no = {(contratos.no_dbt(c) or (None, None))[1]: c for c in por_tabela.values()}
    for e in manifesto["exposures"].values():
        if (e.get("meta") or {}).get("sharing") != "broad":
            continue
        for pai in e["depends_on"]["nodes"]:
            c = por_no.get(manifesto["nodes"][pai]["name"])
            if c is None or classificacao.da_tabela(c).get("sharing") != "broad":
                achados.append(
                    Achado(
                        "classification",
                        False,
                        f"consumidor amplo '{e['name']}' lê '{pai.split('.')[-1]}', que não é amplo",
                        3,
                    )
                )
    for consulta in queries.load(projeto):
        saida = consulta.meta["output_classification"]
        restritas = [
            b
            for b in consulta.meta["bases"]
            if classificacao.da_tabela(por_tabela.get(b, {})).get("sharing") != "broad"
        ]
        if saida["sharing"] == "broad" and restritas and not saida.get("disclosureControl"):
            achados.append(
                Achado(
                    "classification", False, f"{consulta.id}: lê {restritas} e sai ampla sem controle de divulgação", 3
                )
            )
        if saida["nature"] not in ("aggregated", "deidentified") and saida["sharing"] == "broad":
            achados.append(
                Achado("classification", False, f"{consulta.id}: saída ampla com natureza {saida['nature']}", 3)
            )
    if not achados:
        achados.append(
            Achado("classification", True, "consumidores e consultas amplos só publicam o que é publicável", 3)
        )
    return achados


# ---------------------------------------------------------------------------
# Consumo: consultas, métricas, receitas
# ---------------------------------------------------------------------------


def verificar_consumo(projeto: Projeto, manifesto: dict) -> list[Achado]:
    from statera.consume import metrics, queries, recipes

    achados = []
    analyses = {n["name"] for n in manifesto["nodes"].values() if n["resource_type"] == "analysis"}
    por_tabela = contratos.por_tabela(projeto)
    lista = queries.load(projeto)
    for c in lista:
        if not c.sql.exists():
            achados.append(Achado("consumer", False, f"{c.id}: falta {projeto.rel(c.sql)}", 2))
            continue
        if c.dbt_name not in analyses:
            achados.append(Achado("consumer", False, f"{c.id}: SQL não aparece como analysis no dbt", 2))
        if not c.id.startswith(f"{projeto.espaco_de_nomes}.query."):
            achados.append(
                Achado("consumer", False, f"{c.id}: id fora do espaço de nomes {projeto.espaco_de_nomes}", 2)
            )
        achados += [
            Achado("consumer", False, f"{c.id}: base '{b}' sem contrato", 2)
            for b in c.meta["bases"]
            if b not in por_tabela
        ]
        falhas = queries.test(c)
        achados += [Achado("consumer", False, f, 2) for f in falhas]
        if not falhas:
            achados.append(Achado("consumer", True, f"{c.id}: teste com dados sintéticos passou", 2))
    for nome, m in metrics.definitions(projeto).items():
        for fonte in m.get("sources", []):
            contrato = por_tabela.get(fonte["table"])
            if contrato is None:
                achados.append(Achado("consumer", False, f"métrica {nome}: fonte {fonte['table']} sem contrato", 2))
                continue
            colunas = {p["name"] for p in contrato["schema"][0]["properties"]}
            faltam = [d for d in [m["time"], *fonte["dimensions"]] if d not in colunas]
            achados += [
                Achado("consumer", False, f"métrica {nome}: {fonte['table']} não tem a coluna '{d}'", 2) for d in faltam
            ]
        for comp in (m.get("numerator"), m.get("denominator")):
            if comp and comp not in metrics.definitions(projeto):
                achados.append(Achado("consumer", False, f"métrica {nome}: componente '{comp}' não existe", 2))
    achados += [Achado("consumer", False, p, 2) for p in recipes.header_problems(projeto)]
    achados.append(
        Achado(
            "consumer",
            True,
            f"{len(lista)} consultas, {len(metrics.definitions(projeto))} métricas, "
            f"{len(recipes.load(projeto))} receitas",
            2,
        )
    )
    return achados


# ---------------------------------------------------------------------------
# Esqueleto: os mesmos arquivos em todo produto
# ---------------------------------------------------------------------------


def verificar_esqueleto(projeto: Projeto) -> list[Achado]:
    """Todo produto tem os arquivos do esqueleto, com conteúdo ou com `not_applicable: <motivo>`."""
    from statera.projeto import ESQUELETO

    achados = []
    for u in projeto.produtos():
        for rel in ESQUELETO:
            motivo = u.nao_se_aplica(rel)
            if not (u.pasta / rel).exists():
                achados.append(Achado("esqueleto", False, f"{u.nome}: falta {rel} (com conteúdo ou not_applicable)"))
            elif motivo is not None and len(motivo.strip()) < 3:
                achados.append(Achado("esqueleto", False, f"{u.nome}: {rel} declara not_applicable sem motivo"))
        if u.interno and u.tem("7-disseminate/series.yaml"):
            achados.append(Achado("esqueleto", False, f"{u.nome}: produto interno não é divulgado; series.yaml "
                                                      "deve declarar not_applicable"))  # fmt: skip
        if u.interno and u.nao_se_aplica("1-specify/calendar.yaml") is not None:
            achados.append(Achado("esqueleto", False, f"{u.nome}: o calendário de um produto interno é gerado dos "
                                                      "consumidores; rode `statera sync`"))  # fmt: skip
    if not achados:
        achados.append(Achado("esqueleto", True, f"{len(projeto.produtos())} produtos com o esqueleto completo"))
    return achados


# ---------------------------------------------------------------------------
# Acesso: distribuições e visualizações dos produtos de divulgação
# ---------------------------------------------------------------------------


def verificar_acesso(projeto: Projeto, manifesto: dict) -> list[Achado]:
    from statera import acesso, conformidade, portas

    achados = []
    for u in projeto.produtos():
        if u.interno:
            continue
        problemas = acesso.problemas_distribuicoes(u)
        if conformidade._alvo(portas.odps(u)) == 0:  # rascunho: a forma de acesso é pendência do nível 1
            problemas = [p for p in problemas if not p.startswith("produto de divulgação sem forma de acesso")]
        achados += [Achado("acesso", False, f"{u.nome}: {p}") for p in problemas]
    visualizacoes = acesso.visualizacoes(manifesto)
    for e in visualizacoes:
        achados += [Achado("acesso", False, p) for p in acesso.problemas_visualizacao(projeto, e)]
    if not any(not a.ok for a in achados):
        publicos = sum(not u.interno for u in projeto.produtos())
        achados.append(
            Achado(
                "acesso",
                True,
                f"{publicos} produtos de divulgação com forma de acesso; {len(visualizacoes)} visualizações completas",
            )
        )
    return achados


# ---------------------------------------------------------------------------
# Processos, gerados, higiene
# ---------------------------------------------------------------------------


def verificar_processos(projeto: Projeto, manifesto: dict) -> list[Achado]:
    modelos = set(_nos(manifesto)[0])
    papeis = set(governanca.papeis(projeto))
    achados = []
    for fonte in processos.fontes():
        proc = processos.carregar(fonte)
        erros = processos.validar_estrutura(proc)
        tipos = {e["id"]: e["type"] for e in proc["elements"]}
        saindo: dict[str, list] = {}
        for f in proc["flows"]:
            saindo.setdefault(f["from"], []).append(f)
        executavel = any((e.get("statera") or {}).get("implementation") for e in proc["elements"])
        for gid, tipo in tipos.items():
            if executavel and tipo == "exclusiveGateway" and any(not f.get("condition") for f in saindo.get(gid, [])):
                erros.append(f"gateway {gid} com fluxo de saída sem `condicao` (o executor não saberia o caminho)")
        achados += [Achado("processes", False, f"{projeto.rel(fonte)}: {e}", 3) for e in erros]
    for orfao in processos.bpmn_sem_fonte():
        achados.append(Achado("processes", False, f"{projeto.rel(orfao)} sem YAML de origem (statera bpmn import)", 3))
    for p in bpmn.todos():
        erros = bpmn.validar(p, modelos, papeis)
        achados += [Achado("processes", False, e, 3) for e in erros]
        if not erros:
            achados.append(
                Achado(
                    "processes",
                    True,
                    f"{projeto.rel(p.caminho)}: {len(p.tarefas)} tarefas ligadas a papéis e implementações",
                    3,
                )
            )
    return achados


def _gerado(ok: bool, bom: str, ruim: str, nivel: int) -> Achado:
    return Achado("gerados", ok, bom if ok else ruim, nivel)


def verificar_gerados(projeto: Projeto) -> list[Achado]:
    try:
        bpmn_pendentes = [projeto.rel(p) for p in processos.desatualizados()]
    except ValueError as erro:  # YAML inválido: já reportado no grupo "processes"
        bpmn_pendentes = [str(erro).splitlines()[0]]
    dbt_pendentes = [projeto.rel(p) for p in dbt.desatualizados(projeto)]
    from statera import calendario

    calendarios = [projeto.rel(p) for p in calendario.desatualizados(projeto)]
    return [
        _gerado(
            not calendarios,
            "calendários dos produtos internos em dia com os dos consumidores",
            f"calendários desatualizados: {', '.join(calendarios)}: rode `statera sync`",
            1,
        ),
        _gerado(
            governanca.codeowners_atualizado(projeto),
            "CODEOWNERS em dia com os responsáveis",
            "CODEOWNERS desatualizado: rode `statera codeowners`",
            1,
        ),
        _gerado(
            not dbt_pendentes,
            "projeto dbt em dia com as unidades",
            f"desatualizados: {', '.join(dbt_pendentes)}: rode `statera dbt generate`",
            2,
        ),
        _gerado(
            regras.esta_sincronizado(projeto),
            "unit tests das regras sincronizados",
            "unit tests das regras desatualizados: rode `statera rules sync`",
            3,
        ),
        _gerado(
            not bpmn_pendentes,
            "BPMN em dia com os YAML de processo",
            f"BPMN desatualizado em relação a {', '.join(bpmn_pendentes)}: rode `statera bpmn`",
            3,
        ),
    ]


EXTENSOES_DADOS = {".parquet", ".duckdb", ".sav", ".dta", ".xlsx", ".db"}
IGNORAR = {
    "data",
    ".venv",
    ".git",
    "target",
    "logs",
    "dbt_packages",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
}
PADROES_SEGREDO = [
    re.compile(p)
    for p in (
        r"(?i)(password|senha|secret|token)\s*[:=]\s*['\"][^'\"\s]{6,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    )
]


def verificar_higiene(projeto: Projeto) -> list[Achado]:
    achados = []
    for caminho in projeto.raiz.rglob("*"):
        partes = set(caminho.relative_to(projeto.raiz).parts)
        if partes & IGNORAR or not caminho.is_file():
            continue
        if caminho.suffix in EXTENSOES_DADOS or caminho.name.endswith(".csv.gz"):
            achados.append(Achado("higiene", False, f"arquivo de dados fora das pastas data/: {projeto.rel(caminho)}"))
        if (
            caminho.suffix in {".py", ".yml", ".yaml", ".sql", ".json", ".md", ".toml"}
            and caminho.stat().st_size < 2_000_000
        ):
            texto = caminho.read_text(encoding="utf-8", errors="ignore")
            if any(p.search(texto) for p in PADROES_SEGREDO):
                achados.append(Achado("higiene", False, f"possível segredo em {projeto.rel(caminho)}"))
    if not achados:
        achados.append(Achado("higiene", True, "nenhum arquivo de dados nem segredo fora das pastas data/"))
    return achados


def verificar_conformidade(projeto: Projeto, nivel: int | None) -> list[Achado]:
    from statera import conformidade

    achados = []
    for r in conformidade.avaliar(projeto, nivel):
        ok = r.atingido >= r.alvo
        texto = f"{r.unidade.nome}: nível {r.atingido} ({conformidade.NOMES.get(r.atingido, 'none')}), alvo {r.alvo}"
        achados.append(Achado("conformidade", ok, texto, r.alvo))
        if not ok:
            achados += [
                Achado("conformidade", False, f"{r.unidade.nome} nível {n}: {m}", r.alvo)
                for n, m in r.pendencias
                if n <= r.alvo
            ]
    return achados


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def coletar(gerar_manifesto: bool = True, nivel: int | None = None) -> list[Achado]:
    projeto = atual()
    m = dbt.manifesto(gerar=gerar_manifesto, projeto=projeto)
    etapas: list[Callable[[], list[Achado]]] = [
        lambda: verificar_esquemas(projeto),
        lambda: verificar_perfil(projeto),
        lambda: verificar_esqueleto(projeto),
        lambda: _ref_contratos(projeto, m, contratos.todos(projeto)),
        lambda: _ref_unidades(projeto, contratos.todos(projeto)),
        lambda: _ref_consumo_interno(projeto),
        lambda: _ref_regras(projeto, m),
        lambda: _ref_papeis(projeto),
        lambda: _ref_series_indicadores(projeto),
        lambda: verificar_camadas(m),
        lambda: verificar_regras_python(projeto),
        lambda: verificar_consumo(projeto, m),
        lambda: verificar_classificacao(projeto, m),
        lambda: verificar_acesso(projeto, m),
        lambda: verificar_processos(projeto, m),
        lambda: verificar_gerados(projeto),
        lambda: verificar_higiene(projeto),
        lambda: verificar_conformidade(projeto, nivel),
    ]
    achados = [a for etapa in etapas for a in etapa()]
    return [a for a in achados if nivel is None or a.nivel <= nivel]


def executar(gerar_manifesto: bool = True, detalhar: bool = False, nivel: int | None = None) -> int:
    achados = coletar(gerar_manifesto, nivel)
    grupos: dict[str, list[Achado]] = {}
    for a in achados:
        grupos.setdefault(a.grupo, []).append(a)
    if nivel:
        print(f"Validação no nível {nivel} de conformidade\n")
    for grupo, lista in grupos.items():
        falhas = [a for a in lista if not a.ok]
        print(f"{'✔' if not falhas else '✘'} {grupo:<13} {len(lista) - len(falhas)} ok, {len(falhas)} falhas")
        for a in lista:
            if not a.ok or detalhar:
                print(f"    {'ok   ' if a.ok else 'FALHA'} {a.mensagem}")
    total = sum(not a.ok for a in achados)
    print(f"\n{'Repositório válido.' if not total else f'{total} problema(s) encontrado(s).'}")
    return 1 if total else 0
