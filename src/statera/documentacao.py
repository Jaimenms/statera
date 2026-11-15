"""Gera a documentação navegável a partir dos metadados do repositório de conteúdo.

  docs/ (no repositório): só o que muda quando o repositório muda — catálogo, linhagem
  estrutural, processos, regras, séries, consultas, métricas, receitas e conformidade.
  <registro de edições>/<serie>/README.md: o que muda a cada edição — edições, revisões,
  avaliação, linhagem por versão e duração observada.

Nada é escrito à mão, para não desatualizar. Os arquivos funcionam no GitHub/GitLab (Markdown,
Mermaid e SVG) e no preview de Markdown do VS Code.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yaml

from statera import classificacao, contratos, gerado, linhagem
from statera.design import bpmn
from statera.disseminate import registro
from statera.edicao import ler_jsonl
from statera.process import dbt, regras
from statera.projeto import Projeto, Unidade, atual, ler_yaml

EIXOS = ("theme", "product", "bases", "frequency", "purpose")


def _aviso(projeto: Projeto) -> str:
    nota = "**Exemplo com DADOS SINTÉTICOS.**" if projeto.sintetico else None
    return gerado.cabecalho(**_DOCS, estilo="md", nota=nota)


_DOCS = {
    "comando": "statera docs",
    "fontes": "metadados do repositório (contratos, produtos, processos, regras, séries)",
    "regenerar": "statera docs",
    "ci": False,
}


def _link(projeto: Projeto, caminho: Path, texto: str | None = None) -> str:
    rel = os.path.relpath(caminho, projeto.docs).replace(os.sep, "/")
    return f"[{texto or caminho.name}]({rel})"


def _tabela(cabecalho: list[str], linhas: list[list]) -> str:
    def celula(v) -> str:
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")

    saida = ["| " + " | ".join(cabecalho) + " |", "|" + "|".join("---" for _ in cabecalho) + "|"]
    saida += ["| " + " | ".join(celula(v) for v in linha) + " |" for linha in linhas]
    return "\n".join(saida)


def _escrever(projeto: Projeto, nome: str, conteudo: str) -> Path:
    destino = projeto.docs / nome
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo.rstrip() + "\n", encoding="utf-8")
    return destino


def _eixos_produto(odps: dict) -> dict:
    return {c["property"]: c["value"] for c in odps.get("customProperties", [])}


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


ROTULO_ALINHAMENTO = {"source": "interno · fonte", "aggregate": "interno · agregado", "consumer": "divulgação"}


def _malha(projeto: Projeto) -> str:
    """Mermaid: produtos internos e de divulgação, ligados pelas portas de entrada."""
    from statera import portas

    def mid(nome: str) -> str:
        return "p_" + nome.replace("-", "_")

    linhas = ["flowchart LR"]
    for u in projeto.produtos():
        forma = ('[("', '")]') if u.alinhamento == "source" else (('[["', '"]]') if u.interno else ('(["', '"])'))
        rotulo = f"{u.odps.get('name', u.nome)}<br/><small>{ROTULO_ALINHAMENTO[u.alinhamento]}</small>"
        linhas.append(f"  {mid(u.nome)}{forma[0]}{rotulo}{forma[1]}")
    for u in projeto.produtos():
        for entrada in portas.produtos_de_entrada(u):
            linhas.append(f"  {mid(entrada.nome)} -->|corte| {mid(u.nome)}")
    linhas += ["  classDef interno fill:#ECEFF4,stroke:#4C566A", "  classDef publico fill:#FBF0C9,stroke:#9A7B0A"]
    internos = [mid(u.nome) for u in projeto.produtos() if u.interno]
    publicos = [mid(u.nome) for u in projeto.produtos() if not u.interno]
    linhas += [f"  class {','.join(internos)} interno"] if internos else []
    linhas += [f"  class {','.join(publicos)} publico"] if publicos else []
    return "\n".join(linhas)


def _secao_unidades(projeto: Projeto) -> list[str]:
    from statera import conformidade, portas

    niveis = {r.unidade.nome: r for r in conformidade.avaliar(projeto)}
    linhas = []
    for u in projeto.produtos():
        cp = _eixos_produto(u.odps)
        entradas = ", ".join(e.nome for e in portas.produtos_de_entrada(u)) or "—"
        r = niveis[u.nome]
        nivel = f"{r.atingido} — {conformidade.NOMES.get(r.atingido, 'none')}" if r.atingido else "0"
        linhas.append([_link(projeto, u.descritor, u.odps["name"]), ROTULO_ALINHAMENTO[u.alinhamento], nivel,
                       f"`{u.urn}`", u.odps.get("domain", ""), entradas, cp.get("theme", ""),
                       ", ".join(cp.get("bases", [])), cp.get("frequency", "") or "por corte",
                       cp.get("purpose", "")])  # fmt: skip
    return [
        "## Produtos e os cinco eixos do edital",
        "",
        "O item 13 do edital pede organização por **tema, produto, base, periodicidade e finalidade**. Os "
        "eixos vêm do descritor ODPS de cada produto. Produtos **internos** (alinhados à fonte ou agregados) "
        "não são divulgados: liberam **cortes** para os consumidores autorizados. O **nível** é o de "
        "conformidade atingido (1 catalogado a 4 avaliado; detalhes em [conformidade](conformance.md)).",
        "",
        _tabela(
            [
                "Produto",
                "Tipo",
                "Nível",
                "Identificador",
                "Domínio",
                "Lê os cortes de",
                "Tema",
                "Bases",
                "Periodicidade",
                "Finalidade",
            ],
            linhas,
        ),
        "",
        "### Malha de produtos",
        "",
        "```mermaid",
        _malha(projeto),
        "```",
        "",
    ]


def _autoria(autores: list[dict] | None) -> str:
    return ", ".join(f"{a['name']} ({a['unit']})" if a.get("unit") else a["name"] for a in autores or [])


def _url(texto: str, url: str | None) -> str:
    return f"[{texto}]({url})" if url else texto


def _secao_acesso(projeto: Projeto, manifesto: dict) -> list[str]:
    """Distribuições (arquivos) e visualizações (painéis e gráficos) de cada produto de divulgação."""
    from statera import acesso

    linhas = []
    for u in projeto.produtos():
        if u.interno:
            continue
        for d in acesso.distribuicoes(u):
            edicao = _link(projeto, d.registro, d.edicao) if d.registro and d.registro.exists() else (d.edicao or "")
            origem = "gerada a cada edição" if d.origem == "series" else "produzida fora do processo"
            linhas.append([u.odps["name"], _url(d.title, d.url), d.formato, d.atualizacao, edicao, origem])
    visualizacoes = acesso.visualizacoes(manifesto)
    rotulo = {"dashboard": "dashboard", "chart": "gráficos estáticos"}
    linhas_vis = []
    for e in visualizacoes:
        meta = e.get("meta") or {}
        tabelas = ", ".join(f"`{n.split('.')[-1]}`" for n in e["depends_on"]["nodes"])
        linhas_vis.append([_url(e.get("label") or e["name"], e.get("url")), meta.get("product", ""),
                           rotulo.get(meta.get("visualization"), ""), meta.get("tool", ""), meta.get("reads", ""),
                           meta.get("updated", ""), tabelas, (e.get("owner") or {}).get("name", ""),
                           _autoria(meta.get("authors")), e.get("maturity", "")])  # fmt: skip
    partes = [
        "## Como acessar",
        "",
        "Onde o público encontra cada produto de divulgação. As **distribuições** são os arquivos (no "
        "vocabulário do DCAT): as de produtos com série saem do processo a cada edição e ficam registradas, "
        "com hash, no registro de edições; as de produtos de nível 1 são declaradas no descritor ODPS "
        "(`distribuicoes`), porque ainda são produzidas fora do processo.",
        "",
        _tabela(["Produto", "Distribuição", "Formato", "Atualização", "Última edição", "Origem"], linhas),
        "",
    ]
    if linhas_vis:
        partes += [
            "### Painéis e gráficos",
            "",
            "Visualizações declaradas como consumidores (`exposures` do dbt). As públicas leem a **edição "
            "publicada**, para mostrar o mesmo número da planilha; a coluna *Lê* diz o modo de leitura "
            "(`edicao-publicada`, `data-corte` ou `vigente`).",
            "",
            _tabela(
                [
                    "Visualização",
                    "Produto",
                    "Tipo",
                    "Ferramenta",
                    "Lê",
                    "Atualização",
                    "Tabelas",
                    "Dono",
                    "Autoria",
                    "Maturidade",
                ],
                linhas_vis,
            ),  # fmt: skip
            "",
        ]
        for e in visualizacoes:
            graficos = (e.get("meta") or {}).get("charts") or []
            if graficos:
                partes += [
                    f"**{e.get('label') or e['name']}**: cada imagem, os números que ela mostra (em tabela) e o "
                    "texto alternativo.",
                    "",
                    _tabela(
                        ["Gráfico", "Números em tabela (porta)", "Texto alternativo"],
                        [[g.get("title", ""), g.get("port", ""), g.get("alt_text", "")] for g in graficos],
                    ),  # fmt: skip
                    "",
                ]
    return partes


def _secao_autorizacoes(projeto: Projeto) -> list[str]:
    """Quem pode ler cada produto interno, para quê e até quando (authorizedConsumers)."""
    from statera import portas

    linhas = []
    for u in projeto.produtos():
        if not u.interno:
            continue
        for item in u.propriedade("authorizedConsumers", []) or []:
            if item.get("role"):
                quem, tipo = item["role"], "equipe (lê no ambiente do órgão)"
                vigente = "sim" if portas.autorizacao_vigente(u, item["role"]) else "**vencida**"
            else:
                quem = item.get("product") or item.get("query")
                tipo, vigente = ("product" if item.get("product") else "consulta catalogada"), "—"
            linhas.append([u.odps["name"], f"`{quem}`", tipo, item.get("purpose", ""), item.get("until", "—"),
                           vigente, item.get("act", "")])  # fmt: skip
    if not linhas:
        return []
    return [
        "## Quem pode ler os produtos internos",
        "",
        "Produtos internos guardam dado restrito, inclusive pessoal. Quem lê está declarado no produto "
        "(`authorizedConsumers`), sempre com a finalidade. Uma **equipe** que não é a dona lê no "
        "ambiente do órgão, até a data autorizada e com o ato que autorizou; vencido o prazo, o CI falha "
        "até a autorização ser renovada ou retirada. Fora dessas autorizações, o caminho é uma consulta "
        "catalogada (saída agregada, com controle de divulgação) ou os microdados não identificados.",
        "",
        _tabela(["Produto interno", "Quem", "Tipo", "Finalidade", "Até", "Vigente", "Ato"], linhas),
        "",
    ]


def _secao_referencias(projeto: Projeto) -> list[str]:
    linhas = []
    for r in projeto.referencias():
        for caminho in r.contratos():
            c = ler_yaml(caminho)
            prop = contratos.propriedade_custom
            linhas.append([
                _link(projeto, caminho, c["name"]), c["version"], prop(c, "temporality", "—"),
                prop(c, "authority", ""), prop(c, "temporalNote", ""),
            ])  # fmt: skip
    return [
        "## Referências no tempo",
        "",
        "Cada referência declara a temporalidade: **estavel**; **vigencia** (cada linha vale num intervalo, e as "
        "junções usam a data do evento); **versao** (versões publicadas pela autoridade, fixadas pelo consumidor, "
        "com tabela de correspondência entre versões). Toda edição registra a versão e o hash de cada "
        "referência usada.",
        "",
        _tabela(["Referência", "Versão do contrato", "Temporalidade", "Autoridade", "Nota"], linhas),
        "",
    ]


def _secao_camadas(projeto: Projeto, todos: dict) -> list[str]:
    partes = ["## Ativos por camada", ""]
    for camada in ("bronze", "ref", "silver", "gold"):
        itens = [(c, caminho) for caminho, c in todos.values() if contratos.camada(c) == camada]
        if not itens:
            continue
        linhas = []
        for c, caminho in sorted(itens, key=lambda x: contratos.tabela_fisica(x[0])):
            dono = (c.get("team") or {}).get("members", [{}])[0].get("username", "").replace("papel:", "")
            unidade = projeto.unidade_de(caminho)
            linhas.append(
                [
                    f"`{contratos.tabela_fisica(c)}`",
                    unidade.nome if unidade else "",
                    " ".join(c["description"]["purpose"].split()),
                    classificacao.resumo(classificacao.da_tabela(c)),
                    dono,
                    _link(projeto, caminho, c["id"]),
                ]
            )
        partes += [f"### {linhagem.ROTULO_CAMADA[camada]}", ""]
        partes += [_tabela(["Ativo", "Unidade", "Propósito", "Classificação", "Dono", "Contrato"], linhas), ""]
    return partes


def _secao_contrato(projeto: Projeto, cid: str, caminho: Path, c: dict) -> list[str]:
    obj = c["schema"][0]
    partes = [
        f"### {c['name']}",
        "",
        f"`{contratos.propriedade_custom(c, 'urn')}` · `{cid}` · versão {c['version']} · "
        f"{_link(projeto, caminho, 'arquivo do contrato')}",
        "",
        f"- **Classificação:** {classificacao.resumo(classificacao.da_tabela(c))}",
        f"- **Granularidade:** {obj.get('dataGranularityDescription', '')}",
        f"- **Limitações:** {' '.join(c['description'].get('limitations', '').split())}",
        "",
    ]
    linhas = [
        [
            f"`{p['name']}`",
            p.get("logicalType"),
            "sim" if p.get("required") else "",
            p.get("classification"),
            "personal" if classificacao.coluna_pessoal(p) else "",
            p.get("description", ""),
        ]
        for p in obj["properties"]
    ]
    partes += [_tabela(["Coluna", "Tipo", "Obrigatória", "Compartilhamento", "Dado pessoal", "Descrição"], linhas), ""]
    qualidade = list(obj.get("quality", []))
    qualidade += [q | {"column": p["name"]} for p in obj["properties"] for q in p.get("quality", [])]
    if qualidade:
        partes.append("**Regras de qualidade:**")
        for q in qualidade:
            alvo = f" (`{q['column']}`)" if "column" in q else ""
            texto = " ".join((q.get("description") or q.get("metric") or q.get("type")).split())
            partes.append(f"- `{q.get('id', q.get('metric'))}`{alvo}: {texto}")
        partes.append("")
    return partes


def gerar_catalogo(projeto: Projeto) -> Path:
    todos = contratos.todos(projeto)
    manifesto = dbt.manifesto(gerar=False, projeto=projeto)
    partes = ["# Catálogo de dados", "", _aviso(projeto), *_secao_unidades(projeto),
              *_secao_acesso(projeto, manifesto), *_secao_autorizacoes(projeto), *_secao_referencias(projeto),
              *_secao_camadas(projeto, todos)]  # fmt: skip
    partes += ["## Detalhe dos contratos", ""]
    for cid, (caminho, c) in sorted(todos.items()):
        partes += _secao_contrato(projeto, cid, caminho, c)
    return _escrever(projeto, "catalog.md", "\n".join(partes))


# ---------------------------------------------------------------------------
# Linhagem estrutural
# ---------------------------------------------------------------------------


def gerar_linhagem(projeto: Projeto) -> list[Path]:
    g = linhagem.construir()
    svg = _escrever(
        projeto,
        "lineage.svg",
        gerado.cabecalho(**_DOCS, estilo="xml") + linhagem.para_svg(g, f"Linhagem de dados — {projeto.nome}"),
    )
    mermaid = linhagem.para_mermaid(g)
    _escrever(projeto, "lineage.mmd", gerado.cabecalho(**_DOCS, estilo="%%") + mermaid)
    sementes = [n for n in g.nos.values() if n.tipo == "seed"]
    # exemplo de impacto: a referência com mais ativos a jusante
    referencia = max(sementes, key=lambda n: (len(linhagem.impacto(g, n.id)), n.rotulo), default=None)
    partes = [
        "# Linhagem de dados",
        "",
        _aviso(projeto),
        "Gerada do `manifest.json` do dbt, dos contratos ODCS, dos consumidores declarados (`exposures`) "
        "e das consultas recorrentes. A linhagem **por versão** (de qual extração saiu cada número de cada "
        "edição) fica na página da série no registro de edições.",
        "",
        "![Linhagem](lineage.svg)",
        "",
        "<details><summary>Mesma linhagem em Mermaid</summary>",
        "",
        "```mermaid",
        mermaid,
        "```",
        "",
        "</details>",
        "",
    ]
    if referencia:
        afetados = linhagem.impacto(g, referencia.id)
        partes += [
            "## Análise de impacto",
            "",
            f"Exemplo: o que precisa ser revisto se a referência `{referencia.rotulo}` mudar "
            f"(`statera lineage --impact {referencia.rotulo}`).",
            "",
            _tabela(
                ["Camada", "Ativo afetado", "Contrato"],
                [[g.nos[n].camada, g.nos[n].rotulo, g.nos[n].contrato or ""] for n in afetados],
            ),
            "",
        ]
    return [svg, _escrever(projeto, "lineage.md", "\n".join(partes))]


# ---------------------------------------------------------------------------
# Processos
# ---------------------------------------------------------------------------


def _linha_tarefa(p: bpmn.Processo, t: bpmn.Elemento) -> list:
    impl = t.attrs.get("implementation", "")
    return [
        t.nome,
        p.lanes.get(t.lane, {}).get("name", ""),
        t.tipo,
        t.attrs.get("gsbpm"),
        f"{t.duracao:.0f} min",
        f"`{impl}`" if impl else "",
        "exceção" if t.attrs.get("exception") == "true" else "",
    ]


def gerar_processos(projeto: Projeto) -> list[Path]:
    gerados = bpmn.renderizar_todos()
    partes = [
        "# Processos (BPMN 2.0 anotado com GSBPM)",
        "",
        _aviso(projeto),
        "A fonte de cada processo é um **YAML espelho do BPMN 2.0** em `<unidade>/2-design/processes/`. "
        "`statera bpmn` gera o `.bpmn` padrão (Camunda Modeler, bpmn.io, extensão do VS Code) e as imagens "
        "abaixo. O processo to-be é também o **plano de execução**: `statera run` percorre os fluxos, "
        "roda as tarefas automáticas e para nas humanas.",
        "",
    ]
    processos = bpmn.todos()
    resumos = []
    for p in processos:
        r = bpmn.resumo(p)
        resumos.append(
            [
                p.nome,
                r["tarefas"],
                f"{r['manual_h']:.1f} h",
                f"{r['semi-automatic_h']:.1f} h",
                f"{r['automatic_h']:.2f} h",
                f"{r['total_h']:.1f} h",
            ]
        )
    partes += [
        "## Comparação de esforço por edição",
        "",
        _tabela(["Processo", "Tarefas", "Manual", "Semiautomático", "Automático", "Total"], resumos),
        "",
        "Durações modeladas (`statera.duracaoMin`), no caminho normal, sem tarefas de exceção. No as-is, "
        "são **hipóteses a confirmar no diagnóstico (P2)**. A duração observada fica na página da série no "
        "registro de edições.",
        "",
    ]
    for p in processos:
        svg = bpmn.destino_svg(p)
        partes += [
            f"## {p.nome}",
            "",
            f"Fonte: {_link(projeto, p.caminho.with_suffix('.yaml'))} · gerado: {_link(projeto, p.caminho)} · "
            f"[visualizador interativo](processes/{svg.with_suffix('.html').name})",
            "",
            f"![{p.nome}](processes/{svg.name})",
            "",
            _tabela(
                ["Tarefa", "Raia (papel)", "Tipo", "GSBPM", "Duração", "Implementação", ""],
                [_linha_tarefa(p, t) for t in p.tarefas],
            ),
            "",
        ]
    return [*gerados, _escrever(projeto, "processes.md", "\n".join(partes))]


# ---------------------------------------------------------------------------
# Regras e séries
# ---------------------------------------------------------------------------


def gerar_regras(projeto: Projeto) -> Path:
    partes = [
        "# Regras de negócio",
        "",
        _aviso(projeto),
        "Cada regra tem definição em prosa (`rule.md`) e **casos executáveis** (`cases.yaml`), na unidade "
        "que a aplica: no produto interno agregado, se vale para todos os consumidores; no produto de "
        "divulgação, se define o escopo dele. Os casos viram *unit tests* do dbt (`statera rules sync`).",
        "",
    ]
    lista = regras.carregar(projeto)
    linhas = [
        [
            _link(projeto, r.pasta / "rule.md", r.title),
            r.unidade.nome if r.unidade else "",
            f"`{r.modelo}`",
            len(r.casos),
        ]
        for r in lista
    ]
    partes += [_tabela(["Regra", "Unidade", "Modelo dbt", "Casos"], linhas), ""]
    for r in lista:
        partes += [
            f"## {r.title}",
            "",
            f"Implementação: `{r.modelo}` · {_link(projeto, r.pasta / 'cases.yaml', 'cases')}",
            "",
        ]
        for caso in r.casos:
            partes += [
                f"- **{caso['id']}** — {caso['description']}",
                "",
                "  <details><summary>entradas e saída esperada</summary>",
                "",
                "  ```yaml",
            ]
            corpo = yaml.safe_dump(
                {"inputs": caso["inputs"], "expected": caso["expected"]},
                allow_unicode=True,
                sort_keys=False,
                width=200,
            )
            partes += ["  " + linha for linha in corpo.splitlines()]
            partes += ["  ```", "", "  </details>", ""]
    return _escrever(projeto, "rules.md", "\n".join(partes))


def gerar_series(projeto: Projeto) -> Path:
    partes = [
        "# Séries de publicação",
        "",
        _aviso(projeto),
        "Cada série descreve o que **toda** edição publica. As edições são registradas pelo processo, fora "
        f"deste repositório (`{os.path.relpath(projeto.registro_edicoes, projeto.raiz)}`, em statera.toml), "
        "para que publicar "
        "não exija commit no código.",
        "",
    ]
    for u, s in registro.series(projeto):
        odps = u.ler("1-specify/product.odps.yaml")
        arq = s["files_per_edition"]
        tabelas = [arq["tables"].format(porta=p["name"]) for p in odps["outputPorts"]]
        reg = s["edition_registry"]
        gov = s.get("open_data_portal") or {}
        partes += [
            f"## {s['title']}",
            "",
            f"Fonte: {_link(projeto, registro.arquivo_serie(u))} · modelo do sumário: "
            f"{_link(projeto, u.fase(7) / 'summary.md.j2')}",
            "",
            _tabela(
                ["Item", "Valor"],
                [
                    ["Produto", _link(projeto, u.descritor, odps["name"])],
                    ["Periodicidade", s["frequency"]],
                    ["Início", s.get("start", "")],
                    ["Licença", s["license"].get("title", s["license"]["name"])],
                    ["dados.gov.br", f"{gov.get('organization', '')} / {gov.get('dataset', '')}" if gov else ""],
                    [
                        "Arquivos por edição",
                        ", ".join([*tabelas, arq["spreadsheet"], arq["summary"], *arq.get("metadata", [])]),
                    ],
                    ["Porta principal", s["main_resource"]],
                    ["Registro de cada edição", f"`{reg['edition']}`"],
                    ["Índice da série", f"`{reg['index']}`"],
                    ["Quem escreve o registro", reg["written_by"]],
                ],
            ),
            "",
            f"**Cobertura.** {' '.join(s.get('coverage', '').split())}",
            "",
            f"**Revisão.** {' '.join(s['revision_policy'].split())}",
            "",
            f"**Retificação.** {' '.join(s['correction_policy'].split())}",
            "",
        ]
    return _escrever(projeto, "series.md", "\n".join(partes))


# ---------------------------------------------------------------------------
# Camada de consumo: consultas, métricas, receitas
# ---------------------------------------------------------------------------


def gerar_consultas(projeto: Projeto) -> Path:
    from statera.consume import queries

    lista = queries.load(projeto)
    partes = [
        "# Consultas recorrentes",
        "",
        _aviso(projeto),
        "Consultas que se repetem, padronizadas, parametrizadas, testadas e organizadas pelos cinco eixos "
        "do edital. Cada uma é uma *analysis* do dbt (aparece na linhagem) com metadados em `query.yaml`. "
        "Executar: `statera query run <id> --param nome=valor`.",
        "",
    ]
    linhas = [
        [
            _link(projeto, c.pasta / "query.yaml", c.title),
            c.meta.get("theme"),
            c.meta.get("product") or "shared",
            ", ".join(c.meta.get("bases", [])),
            c.meta.get("frequency"),
            c.meta.get("purpose"),
            c.meta.get("status"),
        ]
        for c in lista
    ]
    partes += [_tabela(["Consulta", "Tema", "Produto", "Bases", "Periodicidade", "Finalidade", "Situação"], linhas), ""]
    for c in lista:
        parametros = [
            [
                f"`{p['name']}`",
                p.get("type"),
                "sim" if p.get("required") else "",
                p.get("default", ""),
                p.get("description", ""),
            ]
            for p in c.meta.get("parameters", [])
        ]
        saida = c.meta.get("output_classification", {})
        partes += [
            f"## {c.title}",
            "",
            f"`{c.meta['id']}` · versão {c.meta.get('version')} · responsável `{c.meta.get('owner')}` · "
            f"{_link(projeto, c.sql, 'SQL')}"
            + (f" · autoria: {_autoria(c.meta.get('authors'))}" if c.meta.get("authors") else ""),
            "",
            " ".join(c.meta.get("description", "").split()),
            "",
            _tabela(["Parâmetro", "Tipo", "Obrigatório", "Padrão", "Descrição"], parametros) if parametros else "",
            "",
            f"- **Regras aplicadas:** {', '.join(c.meta.get('rules', [])) or '—'}",
            f"- **Limitações:** {' '.join(c.meta.get('limitations', '').split())}",
            f"- **Classificação da saída:** {classificacao.resumo(saida) if saida else '—'}",
            "",
        ]
    return _escrever(projeto, "queries.md", "\n".join(partes))


def gerar_metricas(projeto: Projeto) -> Path:
    from statera.consume import metrics

    partes = [
        "# Métricas",
        "",
        _aviso(projeto),
        "Cada métrica é definida **uma vez**, com as dimensões permitidas, no formato inspirado na camada "
        "semântica do dbt/MetricFlow. Calcular: `statera metric <nome> --por <dimensão>`, ou "
        "`catalog.open().metrica(...)` em Python.",
        "",
    ]
    linhas = [
        [
            f"`{m['name']}`",
            m.get("label"),
            m["unit"].nome,
            f"`{m['table']}`",
            f"`{m.get('expression') or m.get('type')}`",
            ", ".join(f"`{d}`" for d in m.get("dimensions", [])),
            " ".join(m.get("description", "").split()),
        ]
        for m in metrics.definitions(projeto).values()
    ]
    partes += [_tabela(["Métrica", "Rótulo", "Unidade", "Tabela", "Cálculo", "Dimensões", "Definição"], linhas), ""]
    return _escrever(projeto, "metrics.md", "\n".join(partes))


def gerar_receitas(projeto: Projeto) -> Path:
    from statera.consume import recipes

    partes = [
        "# Receitas",
        "",
        _aviso(projeto),
        "Exemplos curtos e completos para tarefas comuns. Cada receita é executada no CI contra os dados "
        "sintéticos (`statera recipes test`): receita que quebra não passa.",
        "",
    ]
    linhas = [
        [
            _link(projeto, r.caminho, r.title),
            r.meta.get("layers", ""),
            r.meta.get("classification", ""),
            r.caminho.suffix,
            r.meta.get("authors", ""),
        ]
        for r in recipes.load(projeto)
    ]
    partes += [_tabela(["Receita", "Camadas lidas", "Classificação", "Formato", "Autoria"], linhas), ""]
    return _escrever(projeto, "recipes.md", "\n".join(partes))


def gerar_conformidade(projeto: Projeto) -> Path:
    from statera import conformidade

    partes = [
        "# Conformidade",
        "",
        _aviso(projeto),
        "Níveis de conformidade da especificação (1 catalogado, 2 reprodutível, 3 controlado, 4 avaliado). "
        "Cada produto declara o nível-alvo no `product.odps.yaml`; o CI exige o alvo.",
        "",
    ]
    linhas, pendencias = [], []
    for r in conformidade.avaliar(projeto):
        linhas.append([r.unidade.nome, r.alvo, r.atingido, "sim" if r.atingido >= r.alvo else "**não**"])
        pendencias += [f"- **{r.unidade.nome}**, nível {n}: {m}" for n, m in r.pendencias]
    partes += [_tabela(["Produto", "Alvo", "Atingido", "Alvo cumprido"], linhas), ""]
    if pendencias:
        partes += ["## Pendências para o próximo nível", "", *pendencias, ""]
    return _escrever(projeto, "conformance.md", "\n".join(partes))


# ---------------------------------------------------------------------------
# Página da série no registro de edições (muda a cada edição; fora do repositório)
# ---------------------------------------------------------------------------


def _secao_revisoes(unidade: Unidade, dps: list[dict]) -> list[str]:
    from statera.analyse import controles

    principal = registro.serie(unidade)["main_resource"]
    definicao = next(
        (
            c
            for c in (unidade.ler("6-analyse/controls.yaml") or {}).get("controls", [])
            if c["type"] == "revision-between-editions" and c["port"] == principal
        ),
        None,
    )
    if len(dps) < 2 or definicao is None:
        return []
    ult, ant = dps[-1]["x-statera"], dps[-2]["x-statera"]
    atual_, anterior = (registro.tabela_publicada(unidade, x["period"], principal) for x in (ult, ant))
    if atual_ is None or anterior is None:
        return []
    rev = controles.revisoes(atual_, anterior, definicao["key"], definicao["measures"], definicao["relative_base"])
    rev = rev[rev[f"revisao_{definicao['relative_base']}"] != 0]
    colunas = [*definicao["key"], *(f"{m}{s}" for m in definicao["measures"] for s in ("_anterior", ""))]
    return [
        f"## Revisões entre as edições {ant['period']} e {ult['period']}",
        "",
        f"Porta `{principal}`, datas de corte {ant['cutDate']} e {ult['cutDate']} (controle {definicao['id']}).",
        "",
        _tabela(colunas, rev[colunas].values.tolist()),
        "",
    ]


def _secao_avaliacao(unidade: Unidade) -> list[str]:
    from statera.evaluate import avaliacao

    historico = avaliacao.historico(unidade)
    if historico.empty:
        return []
    nomes = {d["id"]: d["name"] for d in avaliacao.indicadores(unidade)["indicators"]}
    linhas = [[f"{c} {nomes.get(c, '')}", *historico[c].tolist()] for c in historico.columns if c != "period"]
    ultima = historico["period"].iloc[-1]
    return [
        "## Avaliação das edições (GSBPM 8)",
        "",
        f"Indicadores declarados em `8-evaluate/indicators.yaml`. ⚠ = em alerta. "
        f"Relatório da última edição: [evaluations/{ultima}.md](evaluations/{ultima}.md).",
        "",
        _tabela(["Indicador", *historico["period"].tolist()], linhas),
        "",
    ]


def _secao_linhagem_versao(dp: dict) -> list[str]:
    x = dp["x-statera"]
    cortes = [[nome, c["cutDate"], f"`{c['run']}`"] for nome, c in (x.get("cuts") or {}).items()]
    extracoes = [
        [
            e["source"],
            e.get("sourceSystem", ""),
            e["cutDate"],
            e.get("batch", ""),
            e["rows"],
            f"`{e['sha256'][:16]}…`",
        ]
        for e in x.get("extractions", [])
    ]
    refs = [
        [r["reference"], r.get("version") or "", r.get("temporality") or "", r.get("authority") or "",
         r.get("via") or "(o próprio produto)", f"`{(r.get('sha256') or '')[:16]}…`"]
        for r in x.get("references", [])
    ]  # fmt: skip
    arquivos = [
        [r["path"], (r.get("x-statera") or {}).get("contract", ""), f"`{r['hash'][7:23]}…`"] for r in dp["resources"]
    ]
    partes = [
        f"## Linhagem por versão — edição {x['period']} (versão {dp['version']})",
        "",
        f"Data de corte **{x['cutDate']}**, execução OpenLineage `{x.get('run')}`, código "
        f"`{x['codeVersion']}`. Cada arquivo publicado é rastreável até os lotes exatos do bronze:",
        "",
    ]
    if cortes:
        partes += [_tabela(["Produto interno de entrada", "Corte", "Execução que liberou o corte"], cortes), ""]
    partes += [
        _tabela(
            ["Extração (bronze)", "Sistema de origem", "Data de corte", "Até o lote", "Linhas", "SHA-256"], extracoes
        ),
        "",
    ]
    if refs:
        partes += [
            _tabela(["Referência", "Versão do contrato", "Temporalidade", "Autoridade", "Usada em", "SHA-256"], refs),
            "",
        ]
    return [*partes, _tabela(["Arquivo publicado", "Contrato", "SHA-256"], arquivos), ""]


def _secao_duracao(unidade: Unidade) -> list[str]:
    execucoes = pd.DataFrame(ler_jsonl(unidade.pasta_execucoes / "execucoes.jsonl"))
    if execucoes.empty:
        return []
    automaticas = execucoes[(execucoes["status"] == "ok") & execucoes["duracao_s"].notna()]
    obs = automaticas.groupby("tarefa_bpmn")["duracao_s"].agg(["count", "mean", "max"]).reset_index()
    linhas = [[r.tarefa_bpmn, r.count, f"{r.mean:.1f}", f"{r.max:.1f}"] for r in obs.itertuples()]
    return [
        "## Duração observada das tarefas automáticas",
        "",
        "Do log de execuções do processo, para comparar com a duração modelada no BPMN.",
        "",
        _tabela(["Tarefa BPMN", "Execuções", "Média (s)", "Máximo (s)"], linhas),
        "",
    ]


def gerar_registro(unidade: Unidade) -> Path | None:
    dps = registro.edicoes(unidade)
    if not dps:
        return None
    indice = registro.caminho_indice(unidade)
    linhas = []
    for dp in dps:
        x = dp["x-statera"]
        arquivo = f"{x['period']}.datapackage.json"
        retifica = (x.get("corrects") or {}).get("version", "")
        linhas.append([x["period"], dp["version"], x["cutDate"], dp["created"], retifica, f"[{arquivo}]({arquivo})"])
    sintetico = " **DADOS SINTÉTICOS.**" if unidade.projeto.sintetico else ""
    partes = [
        f"# {registro.serie(unidade)['title']} — edições publicadas",
        "",
        f"> Página GERADA pelo processo a cada edição publicada. Não edite à mão.{sintetico}",
        "",
        f"Índice da série: [{indice.name}]({indice.name}).",
        "",
        _tabela(["Competência", "Versão", "Data de corte", "Publicado em", "Retifica", "Registro"], linhas),
        "",
        *_secao_avaliacao(unidade),
        *_secao_revisoes(unidade, dps),
        *_secao_linhagem_versao(dps[-1]),
        *_secao_duracao(unidade),
    ]
    destino = indice.parent / "README.md"
    destino.write_text("\n".join(partes).rstrip() + "\n", encoding="utf-8")
    return destino


# ---------------------------------------------------------------------------
# Índice e entrada principal
# ---------------------------------------------------------------------------


def gerar_indice(projeto: Projeto) -> Path:
    from statera import validacao

    achados = validacao.coletar(gerar_manifesto=False)
    falhas = sum(not a.ok for a in achados)
    paginas = [
        ["[Catálogo](catalog.md)", "Unidades pelos 5 eixos do edital, ativos por camada, contratos ODCS"],
        ["[Linhagem](lineage.md)", "Fonte → bronze → silver → gold → consumidores; análise de impacto"],
        ["[Processos](processes.md)", "BPMN as-is e to-be com GSBPM; o to-be é o plano de execução"],
        ["[Regras](rules.md)", "Regras de negócio com casos executáveis, por unidade"],
        ["[Séries](series.md)", "O que cada edição publica e onde as edições são registradas"],
        ["[Consultas](queries.md)", "Consultas recorrentes catalogadas, parametrizadas e testadas"],
        ["[Métricas](metrics.md)", "Métricas oficiais definidas uma vez, com dimensões permitidas"],
        ["[Receitas](recipes.md)", "Exemplos executáveis e testados para quem analisa os dados"],
        ["[Conformidade](conformance.md)", "Nível de conformidade de cada produto e pendências"],
    ]
    partes = [
        f"# Documentação gerada — {projeto.nome}",
        "",
        _aviso(projeto),
        f"Órgão: {projeto.orgao} · espaço de nomes `{projeto.espaco_de_nomes}` · especificação statera "
        f"{projeto.config['project'].get('specification', '?')}.",
        "",
        _tabela(["Página", "Conteúdo"], paginas),
        "",
        "Só entra aqui o que muda quando o repositório muda. O que muda a cada edição (edições publicadas, "
        "revisões, linhagem por versão, duração observada) fica na página da série no registro de edições.",
        "",
        f"Estado da validação no momento da geração: **{len(achados) - falhas} verificações ok, "
        f"{falhas} falhas** (`statera validate`).",
        "",
    ]
    return _escrever(projeto, "README.md", "\n".join(partes))


def gerar_interativo(projeto: Projeto | None = None) -> Path:
    """Catálogo navegável e com busca: o `dbt docs` em página única (`--static`), com os modelos, as
    tabelas de referência, os consumidores e as visualizações, a linhagem e o `meta` de cada tabela
    (produto, contrato, URN e classificação). Fica em target/ (fora do git): contém caminhos da máquina
    que gerou: abre-se direto no navegador, sem servidor."""
    projeto = projeto or atual()
    dbt.garantir_pacotes(projeto)
    processo = dbt.executar(["docs", "generate", "--static"], projeto=projeto)
    if processo.returncode != 0:
        raise RuntimeError(f"dbt docs generate falhou:\n{processo.stdout[-3000:]}")
    return projeto.dbt_projeto / "target" / "static_index.html"


def gerar() -> list[Path]:
    projeto = atual()
    dbt.manifesto(gerar=True, projeto=projeto)
    arquivos = [
        gerar_catalogo(projeto),
        *gerar_linhagem(projeto),
        *gerar_processos(projeto),
        gerar_regras(projeto),
        gerar_series(projeto),
        gerar_consultas(projeto),
        gerar_metricas(projeto),
        gerar_receitas(projeto),
        gerar_conformidade(projeto),
    ]
    registros = [gerar_registro(u) for u, _ in registro.series(projeto)]
    return [*arquivos, gerar_indice(projeto), *filter(None, registros)]
