"""Classificação da informação em quatro dimensões (especificação v0, `03-classificacao.md`).

| Dimensão           | Valores                                                | Fonte                     |
|--------------------|--------------------------------------------------------|---------------------------|
| compartilhamento   | amplo · restrito · especifico                          | Decreto nº 10.046/2019    |
| natureza           | agregado · desidentificado · pessoal · pessoal-sensivel | LGPD (Lei nº 13.709/2018) |
| sigilo             | nenhum · estatistico · fiscal · outro                  | legislação específica; LAI|
| disclosureControl | ausente; cell-suppression (agregados) ou k-anonymity   | GSBPM 6.4                 |
|                    | (microdados desidentificados)                          |                           |

Onde fica cada coisa:
  - no CONTRATO (perfil statera): `customProperties.classification` com as quatro dimensões da
    tabela; em cada coluna, `classification` (amplo | restrito | especifico) e, se for o caso,
    a etiqueta `dado-pessoal` ou `dado-pessoal-sensivel` em `tags`;
  - na APROVAÇÃO (`1-specify/classification.yaml` da unidade, dono: encarregado de dados):
    as mesmas três primeiras dimensões e a base legal. O CI exige que as duas coincidam: quem
    escreve o contrato não aprova sozinho a própria classificação.

Regra de publicação aberta: compartilhamento amplo; natureza agregada ou desidentificada (esta, só
com k-anonymity); sigilo nenhum, ou estatístico com controle de divulgação aplicado; e nenhuma coluna
publicada que não seja ampla ou que seja dado pessoal.
"""

from __future__ import annotations

COMPARTILHAMENTO = ("broad", "restricted", "specific")
NATUREZA = ("aggregated", "deidentified", "personal", "sensitive-personal")
SIGILO = ("none", "statistical", "tax", "other")
TAGS_PESSOAIS = ("dado-pessoal", "dado-pessoal-sensivel")
DIMENSOES = ("sharing", "nature", "secrecy")


def da_tabela(contrato: dict) -> dict:
    from statera.contratos import propriedade_custom

    return propriedade_custom(contrato, "classification") or {}


def controle_exigido(contrato: dict) -> dict | None:
    return da_tabela(contrato).get("disclosureControl")


def coluna_pessoal(prop: dict) -> str | None:
    tags = set(prop.get("tags") or [])
    if "dado-pessoal-sensivel" in tags:
        return "sensitive-personal"
    if "dado-pessoal" in tags:
        return "personal"
    return None


def resumo(classif: dict) -> str:
    partes = [classif.get(d, "?") for d in DIMENSOES]
    if classif.get("disclosureControl"):
        partes.append("com controle de divulgação")
    return " · ".join(partes)


def coerencia(contrato: dict) -> list[str]:
    """Erros de coerência entre a classificação da tabela e a das colunas."""
    erros = []
    tabela = da_tabela(contrato)
    for dim, valores in (("sharing", COMPARTILHAMENTO), ("nature", NATUREZA), ("secrecy", SIGILO)):
        if tabela.get(dim) not in valores:
            erros.append(f"{dim} '{tabela.get(dim)}' fora de {list(valores)}")
    nao_publicadas = set((tabela.get("disclosureControl") or {}).get("unpublishedColumns", []))
    pessoais = []
    for prop in contrato["schema"][0].get("properties", []):
        nivel = prop.get("classification")
        if nivel not in COMPARTILHAMENTO:
            erros.append(f"coluna {prop['name']}: classification '{nivel}' fora de {list(COMPARTILHAMENTO)}")
        if tabela.get("sharing") == "broad" and nivel != "broad" and prop["name"] not in nao_publicadas:
            erros.append(f"coluna {prop['name']} é '{nivel}' numa tabela de compartilhamento amplo")
        if natureza := coluna_pessoal(prop):
            pessoais.append(natureza)
    controle = tabela.get("disclosureControl") or {}
    if controle.get("method") == "k-anonymity":
        colunas = {p["name"] for p in contrato["schema"][0].get("properties", [])}
        citadas = [*controle.get("quasiIdentifiers", []), *controle.get("suppressibleColumns", [])]
        erros += [
            f"k-anonymity: coluna '{c}' não existe no contrato" for c in dict.fromkeys(citadas) if c not in colunas
        ]
        erros += [
            f"k-anonymity: coluna suprimível '{c}' não é quase-identificador"
            for c in controle.get("suppressibleColumns", [])
            if c not in controle.get("quasiIdentifiers", [])
        ]
    if "sensitive-personal" in pessoais and tabela.get("nature") != "sensitive-personal":
        erros.append("há coluna com dado pessoal sensível; natureza da tabela deveria ser pessoal-sensivel")
    elif pessoais and tabela.get("nature") not in ("personal", "sensitive-personal"):
        erros.append("há coluna com dado pessoal; natureza da tabela deveria ser pessoal ou pessoal-sensivel")
    return erros


def impedimentos_publicacao(contrato: dict, colunas_publicadas: list[str], controle_aplicado: bool) -> list[str]:
    """Motivos pelos quais a tabela não pode ir para publicação aberta (lista vazia = pode)."""
    motivos = []
    t = da_tabela(contrato)
    if t.get("sharing") != "broad":
        motivos.append(f"sharing '{t.get('sharing')}' (só 'broad' é publicável)")
    if t.get("nature") not in ("aggregated", "deidentified"):
        motivos.append(f"nature '{t.get('nature')}' (só aggregated ou deidentified)")
    sigilo = t.get("secrecy")
    if sigilo not in ("none", "statistical"):
        motivos.append(f"secrecy '{sigilo}' impede publicação aberta")
    exige_controle = bool(t.get("disclosureControl")) or sigilo == "statistical"
    if exige_controle and not controle_aplicado:
        motivos.append("exige controle de divulgação e ele não foi aplicado")
    if sigilo == "statistical" and not t.get("disclosureControl"):
        motivos.append("sigilo estatístico sem método de controle de divulgação declarado")
    if t.get("nature") == "deidentified" and (t.get("disclosureControl") or {}).get("method") != "k-anonymity":
        motivos.append("microdados desidentificados só saem com controle k-anonymity (risco de reidentificação)")
    for prop in contrato["schema"][0].get("properties", []):
        if prop["name"] not in colunas_publicadas:
            continue
        if prop.get("classification") != "broad":
            motivos.append(f"coluna {prop['name']} é '{prop.get('classification')}' e iria para publicação")
        if coluna_pessoal(prop):
            motivos.append(f"coluna {prop['name']} é dado pessoal e iria para publicação")
    return motivos


def aprovacao_confere(contrato: dict, aprovado: dict | None) -> list[str]:
    """Compara a classificação do contrato com a aprovada em classification.yaml."""
    if aprovado is None:
        return ["sem aprovação em classification.yaml"]
    t = da_tabela(contrato)
    erros = [
        f"{dim}: contrato diz '{t.get(dim)}', classification.yaml diz '{aprovado.get(dim)}'"
        for dim in DIMENSOES
        if t.get(dim) != aprovado.get(dim)
    ]
    metodo = (t.get("disclosureControl") or {}).get("method")
    if metodo != aprovado.get("disclosure_control"):
        erros.append(
            f"controle de divulgação: contrato diz '{metodo}', "
            f"classification.yaml diz '{aprovado.get('disclosure_control')}'"
        )
    return erros
