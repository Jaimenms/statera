"""Controle de divulgação e gate de classificação (GSBPM 6.4 — aplicar controle de divulgação).

Antes de qualquer arquivo ser gerado (fase 7), as tabelas candidatas passam por aqui:
  - o controle de divulgação declarado em `disclosureControl` na classificação do contrato:
      * `cell-suppression` (tabelas agregadas): suprime as medidas onde a contagem fica abaixo do mínimo;
      * `k-anonymity` (microdados desidentificados): toda combinação de quase-identificadores aparece
        em pelo menos k registros; onde não aparece, apaga os valores das colunas suprimíveis, na
        ordem declarada (supressão local, como o `localSuppression` do sdcMicro), e remove o que
        ainda ficar abaixo de k;
  - gate de publicação aberta (Decreto nº 10.046/2019, LGPD, LAI): a classificação do contrato
    precisa coincidir com a aprovada em classification.yaml e permitir publicação aberta.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from statera import classificacao


class ErroClassificacao(RuntimeError):
    """A publicação incluiria um ativo não publicável."""


@dataclass
class Tabela:
    porta: str
    descricao: str
    aba: str
    contrato_id: str
    contrato: dict
    dados: pd.DataFrame
    colunas_nao_publicadas: list[str] = field(default_factory=list)
    colunas_suprimidas: list[str] = field(default_factory=list)
    celulas_suprimidas: int = 0
    linhas_removidas: int = 0
    controle_aplicado: bool = False
    na_planilha: bool = True  # microdados ficam fora do xlsx (porta com inSpreadsheet: false)

    @property
    def objeto(self) -> dict:
        return self.contrato["schema"][0]

    @property
    def tabela_fisica(self) -> str:
        return self.objeto["physicalName"]

    @property
    def controle(self) -> dict | None:
        return classificacao.controle_exigido(self.contrato)


@dataclass
class Resultado:
    dados: pd.DataFrame
    celulas_suprimidas: int = 0
    linhas_removidas: int = 0
    colunas_suprimidas: list[str] = field(default_factory=list)


def _supressao_de_celulas(df: pd.DataFrame, regra: dict) -> Resultado:
    suprimir = df[regra["column"]] < regra["minimum"]
    for col in regra["suppressedColumns"]:
        df[col] = df[col].astype("Int64").mask(suprimir)
    df["suppressed"] = suprimir
    return Resultado(df, int(suprimir.sum()), 0, list(regra["suppressedColumns"]))


def _k_anonimato(df: pd.DataFrame, regra: dict) -> Resultado:
    qi, k = list(regra["quasiIdentifiers"]), int(regra["k"])
    df = df.copy()

    def em_risco(d: pd.DataFrame) -> pd.Series:
        if d.empty:
            return pd.Series(False, index=d.index)
        tamanho = d[qi].astype(object).fillna("∅").groupby(qi, dropna=False)[qi[0]].transform("size")
        return tamanho < k

    suprimidas = 0
    for col in regra["suppressibleColumns"]:
        risco = em_risco(df)
        if not risco.any():
            break
        suprimidas += int((risco & df[col].notna()).sum())
        df[col] = df[col].astype(object).where(~risco, None)
    risco = em_risco(df)
    return Resultado(
        df[~risco].reset_index(drop=True), suprimidas, int(risco.sum()), list(regra["suppressibleColumns"])
    )


METODOS = {"cell-suppression": _supressao_de_celulas, "k-anonymity": _k_anonimato}


def aplicar(df: pd.DataFrame, regra: dict | None) -> Resultado:
    """Aplica o controle declarado (publicação e consultas usam a mesma regra)."""
    if not regra:
        return Resultado(df)
    if regra["method"] not in METODOS:
        raise ErroClassificacao(f"método de controle '{regra['method']}' não suportado")
    resultado = METODOS[regra["method"]](df, regra)
    resultado.dados = resultado.dados.drop(columns=regra.get("unpublishedColumns", []))
    return resultado


def aplicar_controle_divulgacao(tabela: Tabela) -> None:
    """Controle de divulgação (GSBPM 6.4) conforme o contrato do ativo."""
    regra = tabela.controle
    if not regra:
        return
    try:
        r = aplicar(tabela.dados, regra)
    except ErroClassificacao as erro:
        raise ErroClassificacao(f"{tabela.contrato_id}: {erro}") from erro
    tabela.dados = r.dados
    tabela.celulas_suprimidas, tabela.linhas_removidas = r.celulas_suprimidas, r.linhas_removidas
    tabela.colunas_suprimidas = r.colunas_suprimidas
    tabela.colunas_nao_publicadas = list(regra.get("unpublishedColumns", []))
    tabela.controle_aplicado = True


def verificar_publicacao(tabelas: list[Tabela], aprovacao: dict) -> None:
    """Gate: só sai o que está aprovado em classification.yaml e é publicável abertamente."""
    aprovados = {a["contract"]: a for a in aprovacao.get("assets", [])}
    erros = []
    for t in tabelas:
        erros += [
            f"{t.contrato_id}: {e}" for e in classificacao.aprovacao_confere(t.contrato, aprovados.get(t.contrato_id))
        ]
        colunas = [c for c in t.dados.columns if c != "suppressed"]
        motivos = classificacao.impedimentos_publicacao(t.contrato, colunas, t.controle_aplicado)
        erros += [f"{t.contrato_id}: {m}" for m in motivos]
    if erros:
        raise ErroClassificacao("Publicação bloqueada:\n  - " + "\n  - ".join(erros))
