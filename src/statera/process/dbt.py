"""O projeto dbt do órgão: gerado a partir das unidades, executado pela plataforma.

O repositório de conteúdo tem UM projeto dbt, com o `dbt_project.yml` na raiz (ADR 0017) e os
modelos nas unidades:

    products/<fonte>/5-process/models/     staging (leitura do bronze no corte)
    products/<agregado>/5-process/models/  intermediate (silver canônica, por corte)
    products/<consumo>/5-process/models/   intermediate e marts (gold)
    products/<p>/6-analyse/dbt-tests/   controles SQL da edição (testes singulares)
    references/<r>/                      seeds (ref)
    queries/, products/<p>/queries/   analyses do dbt (consultas recorrentes)

Arquivos GERADOS (o CI confere que estão em dia):
    dbt_project.yml                           na raiz: caminhos das unidades e convenções do medalhão
    dbt/generated/_references.yml      propriedades dos seeds, tiradas dos contratos das referências
    dbt/generated/_groups.yml           um grupo dbt por produto (dono), para o controle de acesso

Controle de acesso entre produtos (dbt Core: groups e access): os modelos de um produto INTERNO
são `private` ao grupo dele, exceto os que materializam uma porta de saída (e os staging de um
produto de fonte), que são `public`. Um consumidor que tente ler um modelo privado de outro
produto é barrado pelo dbt na compilação.
    dbt/generated/_rule_unit_tests.yml  (processar/rules.py)

As macros genéricas vêm do pacote dbt `statera` (packages.yml, na raiz), não de cópia.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from statera import classificacao, contratos, gerado
from statera.projeto import MACROS_DBT, Projeto, atual, ler_yaml

CAMADAS_DBT = {
    "staging": {"+schema": "silver", "+materialized": "view", "+meta": {"layer": "silver"}},
    "intermediate": {"+schema": "silver", "+materialized": "table", "+meta": {"layer": "silver"}},
    "marts": {"+schema": "gold", "+materialized": "table", "+meta": {"layer": "gold"}},
}
TIPOS_SEED = {"string": "varchar", "integer": "integer", "number": "double", "date": "date", "boolean": "boolean"}


def _rel(projeto: Projeto, pasta: Path) -> str:
    return os.path.relpath(pasta, projeto.dbt_projeto).replace(os.sep, "/")


def _existentes(projeto: Projeto, pastas) -> list[str]:
    return [_rel(projeto, p) for p in pastas if p.is_dir()]


def gerar_projeto(projeto: Projeto | None = None) -> str:
    projeto = projeto or atual()
    unidades = projeto.produtos()
    conteudo = {
        "name": projeto.dbt_nome,
        "version": "0.1.0",
        "config-version": 2,
        "profile": projeto.dbt_perfil,
        "model-paths": [
            *_existentes(projeto, [u.fase(5) / "models" for u in unidades]),
            _rel(projeto, projeto.dbt_pasta / "generated"),
        ],
        "seed-paths": _existentes(projeto, [r.pasta for r in projeto.referencias()]),
        "test-paths": _existentes(projeto, [u.fase(6) / "dbt-tests" for u in unidades]),
        "macro-paths": [
            _rel(projeto, projeto.dbt_pasta / "macros"),
            *_existentes(projeto, [u.fase(5) / "macros" for u in unidades]),
        ],
        "analysis-paths": _existentes(projeto, projeto.pastas_consultas()),
        "docs-paths": [_rel(projeto, projeto.dbt_pasta / "docs")],
        "target-path": "target",
        "packages-install-path": _rel(projeto, projeto.dbt_pasta / "dbt_packages"),
        "clean-targets": ["target", "logs", _rel(projeto, projeto.dbt_pasta / "dbt_packages")],
        "vars": {"cut_date": "2099-12-31", "period": "2099-12", "batches": {}, **projeto.convencoes},
        "seeds": {projeto.dbt_nome: {"+schema": "ref"}},
        "models": {projeto.dbt_nome: _configuracoes_de_modelos(projeto)},
        "data_tests": {projeto.dbt_nome: {"+severity": "error"}},
    }
    aviso = gerado.cabecalho(
        "statera dbt generate",
        "statera.toml e as unidades (modelos, referências, testes, macros e consultas)",
        nota="Medalhão: bronze = sources (extrações datadas); silver = staging + intermediate; gold = marts;\n"
        "ref = seeds. As variáveis são sobrescritas a cada edição por `statera run`.",
    )
    return aviso + yaml.safe_dump(conteudo, allow_unicode=True, sort_keys=False, width=110)


def _modelos_do_produto(unidade) -> list[tuple[list[str], str]]:
    """(partes do caminho dentro de models/, nome do modelo) de cada modelo do produto, em SQL ou em
    Python (modelos Python do dbt: `def model(dbt, session)`, com pandas)."""
    base = unidade.fase(5) / "models"
    arquivos = [*base.rglob("*.sql"), *(p for p in base.rglob("*.py") if not p.name.startswith("_"))]
    return [(list(a.relative_to(base).parent.parts), a.stem) for a in sorted(arquivos)]


def _acesso(unidade, partes: list[str], modelo: str, publicos: set[str]) -> str | None:
    if not unidade.interno:
        return None  # consumidor: padrão do dbt (protected)
    if modelo in publicos or (partes and partes[0] == "staging"):
        return "public"
    return "private"


def _meta_do_contrato(contrato: dict) -> dict:
    """O que o catálogo sabe da tabela, no `meta` do modelo: aparece (e é buscável) no dbt docs."""
    return {
        "contract": contrato["id"],
        "urn": contratos.propriedade_custom(contrato, "urn"),
        "classification": classificacao.resumo(classificacao.da_tabela(contrato)),
    }


def _grants(projeto: Projeto, unidade, modelo: str, saidas: set[str]) -> dict | None:
    """Permissão de leitura no motor das saídas de um produto interno para as equipes autorizadas
    (`authorizedConsumers` com `papel`, vigentes): a fronteira de verdade, gerada da mesma
    declaração que a validação confere. O DuckDB da demonstração não tem papéis: só em outros motores."""
    from statera import portas

    if projeto.motor["type"] == "duckdb" or not unidade.interno or modelo not in saidas:
        return None
    papeis = sorted(
        {a["role"] for a in portas.papeis_autorizados(unidade) if portas.autorizacao_vigente(unidade, a["role"])}
    )
    return {"select": papeis} if papeis else None


def _configuracoes_de_modelos(projeto: Projeto) -> dict:
    """Configuração por camada (medalhão) e, por modelo, o grupo (produto), o acesso e o contrato."""
    arvore = {camada: dict(conf) for camada, conf in CAMADAS_DBT.items()}
    for unidade in projeto.produtos():
        por_modelo = {}
        for caminho in unidade.contratos():
            c = ler_yaml(caminho)
            if modelo := contratos.propriedade_custom(c, "dbtModel"):
                por_modelo[modelo] = c
        for partes, modelo in _modelos_do_produto(unidade):
            no = arvore
            for parte in partes:
                no = no.setdefault(parte, {})
            config = {"+group": unidade.nome}
            if acesso := _acesso(unidade, partes, modelo, set(por_modelo)):
                config["+access"] = acesso
            if modelo in por_modelo:
                config["+meta"] = {"product": unidade.nome, **_meta_do_contrato(por_modelo[modelo])}
            if grants := _grants(projeto, unidade, modelo, set(por_modelo)):
                config["+grants"] = grants
            no[modelo] = config
    return arvore


def gerar_grupos(projeto: Projeto | None = None) -> str:
    projeto = projeto or atual()
    grupos = [
        {"name": u.nome, "owner": {"name": (u.odps.get("team") or {}).get("name", u.nome)},
         "description": f"Produto {u.nome} ({u.alinhamento}, {'internal' if u.interno else 'publicado'})"}
        for u in projeto.produtos()
    ]  # fmt: skip
    corpo = yaml.safe_dump({"version": 2, "groups": grupos}, allow_unicode=True, sort_keys=False, width=110)
    return gerado.cabecalho("statera dbt generate", "products/*/1-specify/product.odps.yaml") + corpo


def _seed(contrato: dict) -> dict:
    objeto = contrato["schema"][0]
    colunas, tipos = [], {}
    for prop in objeto["properties"]:
        fisico = (prop.get("physicalType") or "").lower()
        tipos[prop["name"]] = fisico or TIPOS_SEED[prop["logicalType"]]
        testes = []
        if prop.get("primaryKey") and _chave_simples(objeto) and prop["name"] != _valido_desde():
            testes += ["unique", "not_null"]
        for rel in prop.get("relationships", []) or []:
            tabela, campo = rel["to"].split(".")
            testes.append({"relationships": {"arguments": {"to": f"ref('{tabela}')", "field": campo}}})
        if testes:
            colunas.append({"name": prop["name"], "data_tests": testes})
    seed = {
        "name": objeto["name"],
        "description": contrato["description"]["purpose"],
        "config": {"column_types": tipos},
        "meta": {
            "layer": "ref",
            "contract": contrato["id"],
            "urn": contratos.propriedade_custom(contrato, "urn"),
            "authority": contratos.propriedade_custom(contrato, "authority"),
        },
    }
    if colunas:
        seed["columns"] = colunas
    if contratos.propriedade_custom(contrato, "temporality") == "validity":
        chave = next(
            (p["name"] for p in objeto["properties"] if p.get("primaryKey") and p["name"] != _valido_desde()), None
        )
        if chave:
            seed["data_tests"] = [{"statera.non_overlapping_validity": {"arguments": {"key": chave}}}]
    return seed


def _valido_desde() -> str:
    return atual().convencoes["valid_from_column"]


def _chave_simples(objeto: dict) -> bool:
    return sum(1 for p in objeto["properties"] if p.get("primaryKey")) == 1


def gerar_referencias(projeto: Projeto | None = None) -> str:
    projeto = projeto or atual()
    seeds = [_seed(ler_yaml(c)) for r in projeto.referencias() for c in r.contratos()]
    corpo = yaml.safe_dump({"version": 2, "seeds": seeds}, allow_unicode=True, sort_keys=False, width=110)
    return gerado.cabecalho("statera dbt generate", "references/*/*.odcs.yaml (contratos das referências)") + corpo


def gerados(projeto: Projeto | None = None) -> dict[Path, str]:
    projeto = projeto or atual()
    return {
        projeto.dbt_projeto / "dbt_project.yml": gerar_projeto(projeto),
        projeto.dbt_pasta / "generated" / "_references.yml": gerar_referencias(projeto),
        projeto.dbt_pasta / "generated" / "_groups.yml": gerar_grupos(projeto),
    }


def sincronizar(projeto: Projeto | None = None) -> list[Path]:
    escritos = []
    if escrever_pacotes(projeto):
        escritos.append((projeto or atual()).dbt_projeto / "packages.yml")
    for caminho, texto in gerados(projeto).items():
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
        escritos.append(caminho)
    return escritos


def desatualizados(projeto: Projeto | None = None) -> list[Path]:
    return [c for c, texto in gerados(projeto).items() if not c.exists() or c.read_text(encoding="utf-8") != texto]


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def executar(argumentos: list[str], variaveis: dict | None = None, projeto: Projeto | None = None):
    """Roda o dbt em subprocesso (isola a conexão DuckDB do dbt da do processo Python)."""
    projeto = projeto or atual()
    dbt = Path(sys.executable).parent / "dbt"
    comando = [
        str(dbt),
        *argumentos,
        "--project-dir",
        str(projeto.dbt_projeto),
        "--profiles-dir",
        str(projeto.dbt_projeto),
    ]
    if variaveis:
        comando += ["--vars", json.dumps(variaveis)]
    projeto.warehouse.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "STATERA_DUCKDB": str(projeto.warehouse), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    return subprocess.run(comando, cwd=projeto.dbt_projeto, env=env, capture_output=True, text=True)


def gerar_pacotes(projeto: Projeto | None = None) -> str:
    """packages.yml do projeto dbt: o pacote dbt da plataforma é o que vem DENTRO do pacote Python instalado.
    Assim há uma só versão a fixar (a do pacote Python, no pyproject.toml) e nenhuma credencial a mais.
    O caminho depende da máquina (ambiente virtual), por isso o arquivo é gerado e fica fora do git."""
    projeto = projeto or atual()
    local = os.path.relpath(MACROS_DBT, projeto.dbt_projeto).replace(os.sep, "/")
    aviso = gerado.cabecalho(
        "statera sync",
        "o pacote statera instalado no ambiente (pyproject.toml)",
        ci=False,
        nota="Fora do git (.gitignore): o caminho é o do ambiente virtual desta máquina.",
    )
    return aviso + f"packages:\n  - local: {local}\n"


def escrever_pacotes(projeto: Projeto | None = None) -> bool:
    """Escreve o packages.yml se mudou; devolve True quando mudou (é preciso rodar dbt deps de novo)."""
    projeto = projeto or atual()
    destino, texto = projeto.dbt_projeto / "packages.yml", gerar_pacotes(projeto)
    if destino.exists() and destino.read_text(encoding="utf-8") == texto:
        return False
    destino.write_text(texto, encoding="utf-8")
    return True


def garantir_pacotes(projeto: Projeto | None = None) -> None:
    """`dbt deps` quando o pacote da plataforma ainda não está instalado no projeto (ou mudou de lugar)."""
    projeto = projeto or atual()
    if escrever_pacotes(projeto) or not (projeto.dbt_pasta / "dbt_packages" / "statera").exists():
        processo = executar(["deps"], projeto=projeto)
        if processo.returncode != 0:
            raise RuntimeError(f"dbt deps falhou:\n{processo.stdout[-2000:]}")


def manifesto(gerar: bool = True, projeto: Projeto | None = None) -> dict:
    """Manifest do dbt (`dbt parse` não precisa de dados)."""
    projeto = projeto or atual()
    if gerar or not projeto.dbt_manifesto.exists():
        garantir_pacotes(projeto)
        processo = executar(["parse"], projeto=projeto)
        if processo.returncode != 0:
            raise RuntimeError(f"dbt parse falhou:\n{processo.stdout[-3000:]}")
    return json.loads(projeto.dbt_manifesto.read_text())


def resultados(projeto: Projeto | None = None) -> list[dict]:
    caminho = (projeto or atual()).dbt_resultados
    return json.loads(caminho.read_text())["results"] if caminho.exists() else []


def falhas(resultados_: list[dict]) -> list[str]:
    return [
        f"{r['unique_id']}: {r['status']} — {(r.get('message') or '').strip()[:300]}"
        for r in resultados_
        if r["status"] in ("error", "fail")
    ]


def contagem_testes(resultados_: list[dict]) -> tuple[int, int]:
    testes = [r for r in resultados_ if r["unique_id"].startswith(("test.", "unit_test."))]
    return len(testes), sum(r["status"] in ("fail", "error") for r in testes)
