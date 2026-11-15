"""Linha de comando `statera`, executada na pasta content/ de um repositório de dados (statera.toml).

Começar:
    statera init meu-repo --name estat-exemplo --namespace br.gov.exemplo --organization "..."
    statera new-product minha-estatistica
Produção (processo to-be do produto):
    statera run --product novo-caged --period 2026-06            # para na 1ª tarefa humana
    statera run --product novo-caged --period 2026-06 --with-dependencies
    statera run --product esocial-eventos --cut-date 2026-07-20   # produto interno: um corte
    statera resume --product novo-caged --period 2026-06          # conclui a tarefa e segue
    statera status --product novo-caged --period 2026-06
    statera cuts                                                  # cortes liberados pelos produtos internos
Qualidade e conformidade:
    statera validate [--level 2]         statera conformance          statera contracts
Arquivos gerados:
    statera sync                         # dbt_project.yml, unit tests das regras, BPMN, CODEOWNERS
Consumo:
    statera query list | run <id> --param uf=SP | test
    statera metric saldo --by regiao --period 2026-06
    statera recipes test
Demonstração:
    statera demo                         # do zero: dados sintéticos, edições, documentação
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _projeto():
    from statera.projeto import atual

    return atual()


def _produto_padrao() -> str:
    """Só há padrão quando o repositório tem um único produto de divulgação; senão, --produto."""
    publicos = [u.nome for u in _projeto().produtos() if not u.interno]
    return publicos[0] if len(publicos) == 1 else ""


# --- produção -------------------------------------------------------------------------------------


def _resultado_edicao(ed) -> int:
    if ed.situacao == "blocked":
        print(f"\n[statera] EDIÇÃO BARRADA ({ed.pendente})", file=sys.stderr)
        for f in ed.falhas[:15]:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


def _cmd_executar(args) -> int:
    from statera import execucao
    from statera.edicao import FalhaNaEdicao

    try:
        ed = execucao.executar(
            args.produto, args.competencia, args.data_corte, args.forcar, args.aprovar,
            com_dependencias=args.com_dependencias,
        )  # fmt: skip
    except FalhaNaEdicao as erro:
        print(f"\n[statera] EDIÇÃO NÃO EXECUTADA\n{erro}", file=sys.stderr)
        return 1
    return _resultado_edicao(ed)


def _cmd_continuar(args) -> int:
    from statera import execucao

    chave = args.competencia or args.data_corte
    ed = execucao.continuar(args.produto, chave, "rejected" if args.reprovar else "approved", args.aprovar)
    return _resultado_edicao(ed)


def _cmd_estado(args) -> int:
    from statera import execucao

    ed = execucao.estado(args.produto, args.competencia or args.data_corte)
    print(
        f"{ed.produto} {ed.competencia} (data de corte {ed.data_corte}): {ed.situacao}"
        + (f", pendente: {ed.pendente}" if ed.pendente else "")
    )
    for h in ed.historico:
        print(f"  {h['tarefa']:<22} {h['status']}{' (simulado)' if h.get('simulada') else ''}")
    return 0


def _cmd_cortes(args) -> int:
    from statera.collect import cortes_liberados, ler_corte

    p = _projeto()
    for u in [p.produto(args.produto)] if args.produto else [u for u in p.produtos() if u.interno]:
        print(f"{u.nome} ({u.alinhamento})")
        for data in cortes_liberados(u):
            m = ler_corte(u, data)
            itens = m.get("sources") or m.get("outputs") or {}
            resumo = ", ".join(
                f"{k}: {v['rows']}" + (f" (lote {v['batch']})" if "batch" in v else "") for k, v in itens.items()
            )
            print(f"  {data}  execução {m['run'][:8]}  {resumo}")
    return 0


def _cmd_sintetico(args) -> int:
    p = _projeto()
    if not p.demo.get("synthetic"):
        print("statera.toml não declara [demo] sintetico", file=sys.stderr)
        return 2
    contagens = p.gancho(p.demo["synthetic"])(p, falha=args.falha, referencias=args.referencias)
    print(f"Fontes sintéticas geradas: {contagens}" + (f" (falha injetada: {args.falha})" if args.falha else ""))
    return 0


# --- qualidade -------------------------------------------------------------------------------------


def _cmd_validar(args) -> int:
    from statera import validacao

    return validacao.executar(gerar_manifesto=not args.sem_dbt, detalhar=args.detalhar, nivel=args.nivel)


def _cmd_conformidade(args) -> int:
    from statera import conformidade

    falhou = False
    for r in conformidade.avaliar(nivel_forcado=args.nivel):
        ok = r.atingido >= r.alvo
        falhou |= not ok
        nome_nivel = conformidade.NOMES.get(r.atingido, "nenhum")
        print(f"{'✔' if ok else '✘'} {r.unidade.nome}: nível {r.atingido} ({nome_nivel}); alvo {r.alvo}")
        for nivel, msg in r.pendencias:
            print(f"    pendência para o nível {nivel}: {msg}")
    return 1 if falhou else 0


def _cmd_contratos(args) -> int:
    from statera.analyse import verificacao

    resultados = verificacao.verificar_todos()
    for r in resultados:
        if args.todos or not r.ok:
            marca = "ok   " if r.ok else ("FALHA" if r.bloqueia else "aviso")
            print(f"[{marca}] {r.contrato:40} {r.verificacao:40} {r.detalhe}")
    falhas = [r for r in resultados if r.bloqueia]
    print(f"{len(resultados)} verificações, {len(falhas)} falhas bloqueantes")
    return 1 if falhas else 0


# --- arquivos gerados -------------------------------------------------------------------------------


def _cmd_regras(args) -> int:
    from statera.process import regras

    if args.acao == "sync":
        print(f"Gerado: {_projeto().rel(regras.sincronizar())}")
        return 0
    if args.acao == "test":
        resultados = regras.testar_casos_python()
        for r in resultados:
            print(f"[{r.situacao:^13}] {r.regra} / {r.caso}" + (f": {r.mensagem}" if r.mensagem else ""))
        falhas = sum(r.situacao == "failed" for r in resultados)
        print(f"{len(resultados)} caso(s) de regras em modelos Python, {falhas} falha(s)")
        return 1 if falhas else 0
    ok = regras.esta_sincronizado()
    print("Unit tests sincronizados com as regras." if ok else "Unit tests DESATUALIZADOS. Rode: statera rules sync")
    return 0 if ok else 1


def _cmd_dbt(args) -> int:
    from statera.process import dbt

    if args.acao == "generate":
        for c in dbt.sincronizar():
            print(f"Gerado: {_projeto().rel(c)}")
        return 0
    pendentes = dbt.desatualizados()
    for c in pendentes:
        print(f"desatualizado: {c} (rode `statera dbt generate`)")
    return 1 if pendentes else 0


def _cmd_bpmn(args) -> int:
    from statera.design import bpmn, processos

    if args.acao == "import":
        if not args.arquivo:
            print("Informe o .bpmn: statera bpmn import <unidade>/2-design/processes/<arquivo>.bpmn", file=sys.stderr)
            return 2
        print(f"YAML atualizado: {processos.importar(Path(args.arquivo).resolve())}")
        return 0
    if args.acao == "check":
        pendentes, orfaos = processos.desatualizados(), processos.bpmn_sem_fonte()
        for p in pendentes:
            print(f"desatualizado: {p} (rode `statera bpmn`)")
        for b in orfaos:
            print(f"sem fonte YAML: {b} (rode `statera bpmn import {b}`)")
        return 1 if pendentes or orfaos else 0
    for caminho in processos.sincronizar():
        print(f"BPMN: {_projeto().rel(caminho)}")
    for caminho in bpmn.renderizar_todos():
        print(f"visualização: {_projeto().rel(caminho)}")
    return 0


def _cmd_codeowners(args) -> int:
    from statera import governanca

    print(f"Gerado: {_rel_fora(governanca.escrever_codeowners())}")
    return 0


def _cmd_sincronizar(args) -> int:
    from statera import calendario

    for caminho in calendario.sincronizar():
        print(f"Calendário (produto interno): {caminho}")
    _cmd_dbt(argparse.Namespace(acao="generate"))
    _cmd_regras(argparse.Namespace(acao="sync"))
    _cmd_bpmn(argparse.Namespace(acao="generate", arquivo=None))
    return _cmd_codeowners(args)


def _cmd_linhagem(args) -> int:
    from statera import linhagem
    from statera.process import dbt

    dbt.manifesto(gerar=True)
    grafo = linhagem.construir()
    if args.impacto:
        afetados = linhagem.impacto(grafo, args.impacto)
        print(f"Alterar '{args.impacto}' afeta {len(afetados)} nós a jusante:")
        for no in afetados:
            print(f"  - [{grafo.nos[no].camada}] {grafo.nos[no].rotulo}")
        return 0
    print(linhagem.para_mermaid(grafo))
    return 0


def _cmd_avaliar(args) -> int:
    from statera.evaluate import avaliacao

    historico = avaliacao.historico(_projeto().produto(args.produto))
    if historico.empty:
        print("Nenhuma edição publicada e avaliada ainda.")
        return 1
    print(historico.to_string(index=False))
    return 0


def _cmd_docs(args) -> int:
    from statera import documentacao

    if getattr(args, "interativo", False):
        print(f"catálogo interativo (abra no navegador): {documentacao.gerar_interativo()}")
        return 0
    for caminho in documentacao.gerar():
        print(f"gerado: {_projeto().rel(caminho)}")
    return 0


# --- consumo -----------------------------------------------------------------------------------------


def _pares(valores: list[str] | None) -> dict:
    saida = {}
    for item in valores or []:
        chave, _, valor = item.partition("=")
        saida[chave.strip()] = valor.strip()
    return saida


def _cmd_consulta(args) -> int:
    from statera.consume import queries

    if args.acao == "list":
        for c in queries.load():
            print(f"{c.id:<60} {c.meta['status']:<11} {c.title}")
        return 0
    if args.acao == "test":
        alvos = [queries.get(args.id)] if args.id else queries.load()
        falhas = [f for c in alvos for f in queries.test(c)]
        for f in falhas:
            print(f"FALHA {f}")
        print(f"{len(alvos)} consulta(s) testada(s), {len(falhas)} falha(s)")
        return 1 if falhas else 0
    df = queries.run(args.id, _pares(args.param), Path(args.saida) if args.saida else None)
    print(df.to_string(index=False))
    print(f"\nprocedência: {df.attrs['procedencia']}")
    return 0


def _cmd_metrica(args) -> int:
    from statera.consume import metrics

    por = [d for d in (args.por or "").split(",") if d]
    periodo = (args.de, args.ate) if args.de and args.ate else None
    df = metrics.compute(args.nome, por, args.competencia, periodo, _pares(args.filtro))
    print(df.to_string(index=False))
    proc = {k: v for k, v in df.attrs["procedencia"].items() if k != "sql"}
    print(f"\nprocedência: {proc}")
    return 0


def _cmd_receitas(args) -> int:
    from statera.consume import recipes

    lista = recipes.load()
    if args.acao == "list":
        for r in lista:
            print(f"{r.caminho.name:<40} {r.title}")
        return 0
    falhas = 0
    for r in lista:
        ok, saida = recipes.run(r)
        falhas += not ok
        print(f"{'ok   ' if ok else 'FALHA'} {r.caminho.name}")
        if not ok or args.mostrar:
            print("    " + saida.replace("\n", "\n    "))
    print(f"{len(lista)} receita(s), {falhas} falha(s)")
    return 1 if falhas else 0


# --- manutenção e demonstração -----------------------------------------------------------------


def _cmd_limpar(args) -> int:
    p = _projeto()
    alvos = [a for pasta in p.pastas_dados() if pasta.exists() for a in pasta.glob("*") if a.name != "README.md"]
    alvos += [p.dbt_projeto / "target", p.dbt_projeto / "logs"]
    if getattr(args, "registro", False):
        alvos.append(p.registro_edicoes)  # só na demonstração: em produção o registro nunca é apagado
    for alvo in alvos:
        if alvo.is_dir():
            shutil.rmtree(alvo)
        elif alvo.exists():
            alvo.unlink()
        else:
            continue
        print(f"removido: {p.rel(alvo)}")
    return 0


def _cmd_novo_produto(args) -> int:
    from copier import run_copy

    from statera.projeto import PACOTE, ler_yaml

    p = _projeto()
    destino = p.raiz / "products" / args.nome
    if destino.exists():
        print(f"{destino} já existe", file=sys.stderr)
        return 1
    # os papéis do produto novo começam iguais aos do que é compartilhado (governance/shared-owners.yaml)
    compartilhado = p.governanca / "shared-owners.yaml"
    papeis = ler_yaml(compartilhado) if compartilhado.exists() else {}
    dados = {
        "product": args.nome,
        "namespace": p.espaco_de_nomes,
        "title": args.titulo or args.nome,
        "alignment": args.alinhamento,
        **{k: papeis[k] for k in ("curator", "technical_owner", "data_protection_officer") if k in papeis},
    }
    modelo = PACOTE / "templates" / "product"
    run_copy(str(modelo), str(destino), data=dados, defaults=True, unsafe=False, quiet=True)
    # o Copier grava o caminho absoluto do modelo: troca por um relativo (não expõe a máquina de quem criou)
    respostas = destino / ".copier-answers.yml"
    if respostas.exists():
        relativo = os.path.relpath(modelo, destino)
        texto = respostas.read_text(encoding="utf-8").replace(str(modelo), relativo)
        respostas.write_text(texto, encoding="utf-8")
    pendente = " Declare authorizedConsumers no product.odps.yaml." if args.alinhamento != "consumer" else ""
    print(f"Produto criado em {p.rel(destino)}.{pendente}")
    print("Próximos passos: preencha 1-specify/ (contrato, classificação, calendário) e rode `make sync validate`.")
    return 0


def _rel_fora(caminho: Path) -> str:
    """Caminho relativo à pasta atual, mesmo fora do conteúdo (o CODEOWNERS fica na raiz do git)."""
    return os.path.relpath(caminho)


def _cmd_init(args) -> int:
    """Cria um repositório de dados do zero a partir do modelo `templates/repository` (Copier)."""
    import re
    import subprocess

    from copier import run_copy

    from statera import __version__
    from statera.projeto import PACOTE

    destino = Path(args.pasta).resolve()
    if destino.exists() and any(destino.iterdir()):
        print(f"{destino} já existe e não está vazia", file=sys.stderr)
        return 1
    nome = args.nome or destino.name
    if not re.fullmatch(r"[a-z0-9-]+", nome):
        print(f"--name deve ter só minúsculas, números e hífens: {nome!r}", file=sys.stderr)
        return 2
    dados = {
        "name": nome,
        "organization": args.organizacao or nome,
        "namespace": args.espaco_de_nomes or f"org.example.{nome}",
        "acronym": args.sigla or nome,
        "statera_git": args.fonte,
        "statera_version": args.versao or __version__,
    }
    modelo = PACOTE / "templates" / "repository"
    run_copy(str(modelo), str(destino), data=dados, defaults=True, unsafe=False, quiet=True)
    respostas = destino / ".copier-answers.yml"
    if respostas.exists():  # sem o caminho absoluto da máquina de quem criou
        texto = respostas.read_text(encoding="utf-8")
        respostas.write_text(texto.replace(str(modelo), f"statera:templates/repository@{__version__}"), "utf-8")
    dentro_de_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=destino, capture_output=True, text=True
    ).returncode == 0  # fmt: skip
    if not dentro_de_git and shutil.which("git") and not args.sem_git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=destino, check=False)
    print(f"Repositório de dados criado em {destino}.")
    print("Próximos passos:")
    relativo = os.path.relpath(destino)
    print(f"  cd {relativo if not relativo.startswith('../..') else destino}")
    print("  make setup                              # ambiente e Statera na versão fixada")
    print("  make validate                           # o repositório nasce válido")
    print("  make new-product NAME=minha-estatistica # a primeira estatística")
    if dados["namespace"].startswith("org.example."):
        print(f"Atenção: espaço de nomes provisório ({dados['namespace']}); ajuste em content/statera.toml.")
    return 0


def _cmd_demo(args) -> int:
    from statera import documentacao, execucao

    p = _projeto()
    _cmd_limpar(argparse.Namespace(registro=True))
    _cmd_sincronizar(args)
    _cmd_sintetico(argparse.Namespace(falha=None, referencias=False))
    for edicao in p.demo.get("editions", []):
        print(f"\n[statera] ==== {edicao['product']} {edicao['period']} ====", flush=True)
        ed = execucao.executar(edicao["product"], edicao["period"], aprovar=True, com_dependencias=True)
        if ed.situacao != "done":
            return _resultado_edicao(ed) or 1
    for caminho in documentacao.gerar():
        print(f"gerado: {p.rel(caminho)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="statera", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="comando", required=True)
    produto = _produto_padrao_seguro()

    s = sub.add_parser("run", help="executa uma edição (ou, num produto interno, um corte) pelo processo to-be")
    s.add_argument("--product", dest="produto", default=produto)
    s.add_argument("--period", dest="competencia", help="obrigatória em produto de divulgação")
    s.add_argument("--cut-date", dest="data_corte", help="padrão: a do calendar.yaml; obrigatória em produto interno")
    s.add_argument(
        "--with-dependencies",
        dest="com_dependencias",
        action="store_true",
        help="libera antes os cortes que faltam nos produtos internos de entrada",
    )
    s.add_argument(
        "--force", dest="forcar", action="store_true", help="republica (retificação) uma edição já publicada"
    )
    s.add_argument("--approve", dest="aprovar", action="store_true", help="simula as tarefas humanas (demonstração)")
    s.set_defaults(func=_cmd_executar)

    s = sub.add_parser("resume", help="conclui a tarefa humana pendente (ou reavalia a espera) e retoma")
    s.add_argument("--product", dest="produto", default=produto)
    s.add_argument("--period", dest="competencia")
    s.add_argument("--cut-date", dest="data_corte", help="em produto interno, a chave da execução")
    s.add_argument("--reject", dest="reprovar", action="store_true", help="registra a decisão como reprovada")
    s.add_argument("--approve", dest="aprovar", action="store_true", help="simula as tarefas humanas seguintes")
    s.set_defaults(func=_cmd_continuar)

    s = sub.add_parser("status", help="situação de uma edição e tarefas concluídas")
    s.add_argument("--product", dest="produto", default=produto)
    s.add_argument("--period", dest="competencia")
    s.add_argument("--cut-date", dest="data_corte")
    s.set_defaults(func=_cmd_estado)

    s = sub.add_parser("cuts", help="cortes liberados pelos produtos internos")
    s.add_argument("--product", dest="produto")
    s.set_defaults(func=_cmd_cortes)

    s = sub.add_parser("synthetic", help="gera as fontes sintéticas (gancho [demo] sintetico)")
    s.add_argument(
        "--fault", dest="falha", choices=["distribuicao"], help="injeta um erro para demonstrar os controles"
    )
    s.add_argument(
        "--references", dest="referencias", action="store_true", help="regenera também as referências sintéticas"
    )
    s.set_defaults(func=_cmd_sintetico)

    s = sub.add_parser("validate", help="validação estática do repositório")
    s.add_argument("--level", dest="nivel", type=int, choices=[1, 2, 3, 4], help="verifica só o que o nível exige")
    s.add_argument(
        "--no-dbt", dest="sem_dbt", action="store_true", help="usa o manifest existente (não roda dbt parse)"
    )
    s.add_argument("--verbose", dest="detalhar", action="store_true", help="lista também as verificações que passaram")
    s.set_defaults(func=_cmd_validar)

    s = sub.add_parser("conformance", help="nível de conformidade de cada produto")
    s.add_argument("--level", dest="nivel", type=int, choices=[1, 2, 3, 4])
    s.set_defaults(func=_cmd_conformidade)

    s = sub.add_parser("contracts", help="verifica os contratos ODCS contra o motor")
    s.add_argument("--all", dest="todos", action="store_true")
    s.set_defaults(func=_cmd_contratos)

    s = sub.add_parser("rules", help="unit tests gerados das regras (SQL) e casos das regras em modelos Python")
    s.add_argument("acao", choices=["sync", "check", "test"])
    s.set_defaults(func=_cmd_regras)

    s = sub.add_parser("dbt", help="dbt_project.yml e propriedades das referências, gerados das unidades")
    s.add_argument("acao", choices=["generate", "check"])
    s.set_defaults(func=_cmd_dbt)

    s = sub.add_parser("bpmn", help="gera .bpmn dos YAML de processo e renderiza SVG/HTML")
    s.add_argument("acao", nargs="?", default="generate", choices=["generate", "import", "check"])
    s.add_argument("arquivo", nargs="?")
    s.set_defaults(func=_cmd_bpmn)

    sub.add_parser("codeowners", help="gera .github/CODEOWNERS").set_defaults(func=_cmd_codeowners)
    sub.add_parser("sync", help="regenera todos os arquivos gerados").set_defaults(func=_cmd_sincronizar)

    s = sub.add_parser("lineage", help="linhagem em Mermaid ou análise de impacto")
    s.add_argument("--impact", dest="impacto")
    s.set_defaults(func=_cmd_linhagem)

    s = sub.add_parser("evaluate", help="indicadores de qualidade das edições publicadas (fase 8)")
    s.add_argument("--product", dest="produto", default=produto)
    s.set_defaults(func=_cmd_avaliar)

    s = sub.add_parser("docs", help="regenera docs/ e a página do registro de edições")
    s.add_argument(
        "--interactive",
        dest="interativo",
        action="store_true",
        help="gera o catálogo navegável e com busca (dbt docs em página única) em target/static_index.html",
    )
    s.set_defaults(func=_cmd_docs)

    s = sub.add_parser("query", help="consultas recorrentes catalogadas")
    s.add_argument("acao", choices=["list", "run", "test"])
    s.add_argument("id", nargs="?")
    s.add_argument("--param", action="append", help="nome=valor (repetível)")
    s.add_argument("--output", dest="saida", help="pasta para resultado.csv e datapackage.json de procedência")
    s.set_defaults(func=_cmd_consulta)

    s = sub.add_parser("metric", help="calcula uma métrica oficial")
    s.add_argument("nome")
    s.add_argument("--by", dest="por", help="dimensões separadas por vírgula")
    s.add_argument("--period", dest="competencia")
    s.add_argument("--from", dest="de")
    s.add_argument("--to", dest="ate")
    s.add_argument("--filter", dest="filtro", action="append", help="dimensao=valor (repetível)")
    s.set_defaults(func=_cmd_metrica)

    s = sub.add_parser("recipes", help="lista ou executa as receitas")
    s.add_argument("acao", choices=["list", "test"])
    s.add_argument("--show", dest="mostrar", action="store_true", help="mostra a saída de cada receita")
    s.set_defaults(func=_cmd_receitas)

    s = sub.add_parser("init", help="cria um repositório de dados do zero (content/, publications/, docs/)")
    s.add_argument("pasta", help="pasta a criar (vazia ou inexistente)")
    s.add_argument("--name", dest="nome", help="nome do repositório (minúsculas e hífens); padrão: nome da pasta")
    s.add_argument("--organization", dest="organizacao", help="órgão ou programa, por extenso")
    s.add_argument("--namespace", dest="espaco_de_nomes", help="espaço de nomes, ex. br.gov.exemplo")
    s.add_argument("--acronym", dest="sigla", help="sigla curta (padrão: o nome)")
    s.add_argument(
        "--statera-source", dest="fonte", default="ssh://git@github.com/Jaimenms/statera.git",
        help="repositório git de onde o pyproject.toml instala a Statera",
    )  # fmt: skip
    s.add_argument("--statera-version", dest="versao", help="versão (tag v<versão>); padrão: a instalada")
    s.add_argument("--no-git", dest="sem_git", action="store_true", help="não roda git init")
    s.set_defaults(func=_cmd_init)

    s = sub.add_parser("new-product", help="cria um produto a partir do modelo (Copier)")
    s.add_argument("nome")
    s.add_argument("--title", dest="titulo")
    s.add_argument(
        "--alignment", dest="alinhamento", choices=["consumer", "aggregate", "source"], default="consumer",
        help="consumer = divulgação; aggregate ou source = produto interno (especificação 09)",
    )  # fmt: skip
    s.set_defaults(func=_cmd_novo_produto)

    s = sub.add_parser("clean", help="apaga as pastas data/ das unidades e os artefatos do dbt")
    s.add_argument(
        "--registry", dest="registro", action="store_true", help="apaga também o registro de edições (só demonstração)"
    )
    s.set_defaults(func=_cmd_limpar)

    sub.add_parser("demo", help="demonstração do zero (apaga dados e registro de edições)").set_defaults(func=_cmd_demo)

    args = p.parse_args(argv)
    if getattr(args, "produto", "x") == "" and args.comando in ("run", "resume", "status", "evaluate"):
        p.error("informe --produto")
    if args.comando in ("resume", "status") and not (args.competencia or args.data_corte):
        p.error("informe --competencia (ou --data-corte, em produto interno)")
    if args.comando == "query" and args.acao == "run" and not args.id:
        p.error("informe o id da consulta")
    if args.comando in ("run", "resume", "status", "evaluate") and not args.produto:
        nomes = ", ".join(u.nome for u in _projeto().produtos())
        print(f"informe --produto (produtos deste repositório: {nomes})", file=sys.stderr)
        return 2
    return args.func(args)


def _produto_padrao_seguro() -> str:
    try:
        return _produto_padrao()
    except Exception:  # fora de um repositório de conteúdo: só o --help funciona
        return ""


if __name__ == "__main__":
    sys.exit(main())
