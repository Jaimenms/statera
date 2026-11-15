"""Referências no tempo: temporalidade declarada e versões usadas por cada edição ou corte.

Referências (municípios, CNAE, CBO, salário mínimo…) mudam: município novo é instalado, a CNAE ganha
versão, a CBO é atualizada. O contrato de cada referência declara a TEMPORALIDADE
(`customProperties.temporality`, especificação 09):

    estavel    a mesma tabela vale para todo o período (ex.: grandes regiões)
    vigencia   cada linha vale num intervalo [vigencia_inicio, vigencia_fim]; as junções usam a data
               do evento (ex.: municípios pela data de instalação, salário mínimo)
    versao     a referência é publicada em versões (releases) que não se misturam; o consumidor fixa
               a versão e, entre versões, usa a tabela de correspondência da autoridade (ex.: CNAE
               2.0 -> 2.3, CBO)

E toda edição publicada e todo corte de produto interno registram a versão do contrato e o hash do
conteúdo de cada referência usada, como registram o corte do eSocial. A referência usada DENTRO de um
produto interno de entrada vem do manifesto do corte (o hash do momento em que o corte foi
calculado, campo `via`), não do arquivo atual: o consumidor lê a partição do corte, não a recalcula.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from statera import contratos
from statera.projeto import Projeto, Unidade, atual

TEMPORALIDADES = ("stable", "validity", "versioned")


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _do_produto(projeto: Projeto, unidade: Unidade, no: dict) -> bool:
    caminho = (projeto.dbt_projeto / no.get("original_file_path", "")).resolve()
    return unidade.pasta in caminho.parents


def _de_outro_produto(projeto: Projeto, unidade: Unidade, no: dict) -> bool:
    dono = projeto.unidade_de(projeto.dbt_projeto / no.get("original_file_path", ""))
    return dono is not None and dono.tipo == "product" and dono != unidade


def _dos_cortes(cortes: dict) -> list[dict]:
    """Referências registradas nos manifestos de corte dos produtos de entrada (e dos de antes deles)."""
    saida = []
    for nome, manifesto in (cortes or {}).items():
        saida += [{**r, "via": r.get("via") or nome} for r in manifesto.get("references") or []]
        saida += _dos_cortes(manifesto.get("inputs") or {})
    return saida


def usadas(unidade: Unidade, manifesto: dict, cortes: dict | None = None) -> list[dict]:
    """Referências (seeds) de que a unidade depende, com versão, temporalidade e hash.

    As dos modelos da própria unidade vêm do arquivo atual; as usadas dentro dos produtos internos
    de entrada vêm dos manifestos dos cortes fixados (`cortes`), com o campo `via`.
    """
    projeto = unidade.projeto
    nos = manifesto["nodes"]
    pilha = [uid for uid, n in nos.items() if n["resource_type"] == "model" and _do_produto(projeto, unidade, n)]
    vistos, sementes = set(), set()
    while pilha:
        uid = pilha.pop()
        if uid in vistos or uid not in nos:
            continue
        vistos.add(uid)
        if nos[uid]["resource_type"] == "seed":
            sementes.add(uid)
            continue
        if _de_outro_produto(projeto, unidade, nos[uid]):
            continue  # lido da partição do corte: as referências dele estão no manifesto do corte
        pilha += nos[uid]["depends_on"]["nodes"]
    por_seed = {contratos.propriedade_custom(c, "dbtSeed"): c for _, c in contratos.todos(projeto).values()}
    saida = []
    for uid in sorted(sementes):
        no = nos[uid]
        contrato = por_seed.get(no["name"], {})
        arquivo = (projeto.dbt_projeto / no["original_file_path"]).resolve()
        saida.append({
            "reference": no["name"],
            "contract": contrato.get("id"),
            "urn": contratos.propriedade_custom(contrato, "urn") if contrato else None,
            "version": contrato.get("version"),
            "temporality": contratos.propriedade_custom(contrato, "temporality", "stable") if contrato else None,
            "authority": contratos.propriedade_custom(contrato, "authority") if contrato else None,
            "sha256": _sha256(arquivo) if arquivo.exists() else None,
            "via": None,
        })  # fmt: skip
    vistas = {(r["reference"], r["via"]) for r in saida}
    for r in _dos_cortes(cortes):
        if (r["reference"], r["via"]) not in vistas:
            vistas.add((r["reference"], r["via"]))
            saida.append(r)
    return saida


def problemas(contrato: dict) -> list[str]:
    """Coerência da temporalidade declarada com as colunas da referência."""
    temporalidade = contratos.propriedade_custom(contrato, "temporality")
    if temporalidade is None:
        return ["sem customProperties.temporality (stable, validity ou versioned)"]
    if temporalidade not in TEMPORALIDADES:
        return [f"temporalidade '{temporalidade}' fora de {list(TEMPORALIDADES)}"]
    colunas = {p["name"] for p in contrato["schema"][0]["properties"]}
    conv = atual().convencoes
    inicio, fim = conv["valid_from_column"], conv["valid_to_column"]
    if temporalidade == "validity" and not {inicio, fim} <= colunas:
        return [f"temporalidade 'validity' exige as colunas {inicio} e {fim} ([conventions] do statera.toml)"]
    return []
