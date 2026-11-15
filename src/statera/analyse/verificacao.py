"""Verificação dos contratos ODCS v3.1 contra os dados no motor (GSBPM 5.3 e 6.2).

Cobre o subconjunto do padrão usado neste repositório:
  - esquema: colunas existentes, tipo lógico compatível, tipo físico, `required`, `unique`,
    chave primária (inclusive composta);
  - qualidade `library`: rowCount, nullValues, missingValues, duplicateValues, invalidValues;
  - qualidade `sql`: consulta que devolve um número, comparado com os operadores mustBe...

Em produção, a ferramenta de referência é o `datacontract-cli` (`datacontract test`), que lê os
mesmos arquivos. Este verificador existe para a demonstração rodar sem dependências pesadas.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from statera import contratos
from statera.projeto import Projeto, atual

TIPOS_COMPATIVEIS = {
    "string": ("VARCHAR",),
    "integer": ("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT"),
    "number": ("DOUBLE", "FLOAT", "DECIMAL", "REAL"),
    "date": ("DATE",),
    "timestamp": ("TIMESTAMP",),
    "boolean": ("BOOLEAN",),
}

OPERADORES = {
    "mustBe": lambda v, r: v == r,
    "mustNotBe": lambda v, r: v != r,
    "mustBeGreaterThan": lambda v, r: v > r,
    "mustBeGreaterOrEqualTo": lambda v, r: v >= r,
    "mustBeLessThan": lambda v, r: v < r,
    "mustBeLessOrEqualTo": lambda v, r: v <= r,
    "mustBeBetween": lambda v, r: r[0] <= v <= r[1],
    "mustNotBeBetween": lambda v, r: not (r[0] <= v <= r[1]),
}


@dataclass
class Resultado:
    contrato: str
    verificacao: str
    ok: bool
    detalhe: str
    severidade: str = "error"

    @property
    def bloqueia(self) -> bool:
        return not self.ok and self.severidade == "error"


def _operador(regra: dict) -> tuple[str, object]:
    for nome in OPERADORES:
        if nome in regra:
            return nome, regra[nome]
    raise ValueError(f"Regra de qualidade sem operador: {regra}")


def _metrica_library(con, tabela: str, regra: dict, coluna: str | None) -> float:
    metrica = regra["metric"]
    if metrica == "rowCount":
        return con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
    if coluna is None:
        raise ValueError(f"Métrica {metrica} exige coluna")
    if metrica == "nullValues":
        return con.execute(f"SELECT count(*) FROM {tabela} WHERE {coluna} IS NULL").fetchone()[0]
    if metrica == "missingValues":
        sql = f"SELECT count(*) FROM {tabela} WHERE {coluna} IS NULL OR CAST({coluna} AS VARCHAR) = ''"
        return con.execute(sql).fetchone()[0]
    if metrica == "duplicateValues":
        sql = f"SELECT count({coluna}) - count(DISTINCT {coluna}) FROM {tabela}"
        return con.execute(sql).fetchone()[0]
    if metrica == "invalidValues":
        validos = regra.get("arguments", {}).get("validValues", [])
        lista = ", ".join("'" + str(v).replace("'", "''") + "'" for v in validos)
        sql = f"SELECT count(*) FROM {tabela} WHERE {coluna} IS NOT NULL AND CAST({coluna} AS VARCHAR) NOT IN ({lista})"
        return con.execute(sql).fetchone()[0]
    raise ValueError(f"Métrica não suportada: {metrica}")


def _filtro_corte(contrato: dict, corte: str | None) -> str | None:
    """Tabelas que guardam vários cortes são verificadas no corte da execução (perfil statera)."""
    if not corte:
        return None
    if particao := contratos.propriedade_custom(contrato, "partitionedBy"):
        return f"{particao} = CAST('{corte}' AS DATE)"
    if extracao := contratos.propriedade_custom(contrato, "extraction"):
        return f"CAST({extracao['receivedAtColumn']} AS DATE) <= CAST('{corte}' AS DATE)"
    return None


def _avaliar_qualidade(
    con, cid: str, tabela: str, regra: dict, coluna: str | None, fisica: str | None = None
) -> Resultado:
    nome = regra.get("id") or regra.get("name") or regra.get("metric") or "qualidade"
    alvo = f"{coluna}.{nome}" if coluna else nome
    severidade = regra.get("severity", "error")
    try:
        tipo = regra.get("type", "library")
        if tipo == "sql":
            consulta = regra["query"].replace(fisica, tabela) if fisica and fisica != tabela else regra["query"]
            valor = con.execute(consulta).fetchone()[0]
        elif tipo == "library" or "metric" in regra:
            valor = _metrica_library(con, tabela, regra, coluna)
        else:
            return Resultado(cid, alvo, True, f"tipo '{tipo}' não executável aqui", "info")
        operador, referencia = _operador(regra)
        ok = OPERADORES[operador](float(valor), referencia)
        detalhe = f"valor={valor} {operador} {referencia}"
        if regra.get("description"):
            detalhe += f" — {regra['description'].strip()}"
        return Resultado(cid, alvo, ok, detalhe, severidade)
    except duckdb.Error as erro:
        return Resultado(cid, alvo, False, f"erro ao executar: {erro}", severidade)


def _verificar_propriedade(con, cid: str, tabela: str, prop: dict, colunas_reais: dict) -> list[Resultado]:
    col = prop["name"]
    if col not in colunas_reais:
        return [Resultado(cid, f"{col}.existe", False, "coluna ausente")]
    resultados = []
    tipo_real = colunas_reais[col]
    logico = prop.get("logicalType")
    if logico and not tipo_real.startswith(TIPOS_COMPATIVEIS.get(logico, (tipo_real,))):
        resultados.append(Resultado(cid, f"{col}.tipo", False, f"lógico {logico} incompatível com {tipo_real}"))
    fisico = (prop.get("physicalType") or "").upper().replace(" ", "")
    if fisico and fisico != tipo_real.replace(" ", ""):
        resultados.append(Resultado(cid, f"{col}.tipo_fisico", False, f"esperado {fisico}, encontrado {tipo_real}"))
    if prop.get("required"):
        nulos = con.execute(f"SELECT count(*) FROM {tabela} WHERE {col} IS NULL").fetchone()[0]
        resultados.append(Resultado(cid, f"{col}.required", nulos == 0, f"nulos={nulos}"))
    if prop.get("unique"):
        dup = con.execute(f"SELECT count({col}) - count(DISTINCT {col}) FROM {tabela}").fetchone()[0]
        resultados.append(Resultado(cid, f"{col}.unique", dup == 0, f"duplicados={dup}"))
    resultados += [_avaliar_qualidade(con, cid, tabela, regra, col) for regra in prop.get("quality", []) or []]
    return resultados


def verificar(contrato: dict, con: duckdb.DuckDBPyConnection, corte: str | None = None) -> list[Resultado]:
    cid = contrato["id"]
    resultados: list[Resultado] = []
    filtro = _filtro_corte(contrato, corte)
    for objeto in contrato.get("schema", []):
        fisica = objeto.get("physicalName", objeto["name"])
        esquema, nome = fisica.split(".")
        tabela = f"(SELECT * FROM {fisica} WHERE {filtro})" if filtro else fisica
        colunas_reais = {
            linha[0]: linha[1].upper()
            for linha in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ?",
                [esquema, nome],
            ).fetchall()
        }
        if not colunas_reais:
            resultados.append(Resultado(cid, fisica, False, "tabela inexistente no warehouse"))
            continue

        chaves = []
        for prop in objeto.get("properties", []):
            resultados += _verificar_propriedade(con, cid, tabela, prop, colunas_reais)
            if prop.get("primaryKey") and prop["name"] in colunas_reais:
                chaves.append((prop.get("primaryKeyPosition", 1), prop["name"]))

        if len(chaves) > 1:
            cols = ", ".join(c for _, c in sorted(chaves))
            dup = con.execute(
                f"SELECT count(*) FROM (SELECT {cols} FROM {tabela} GROUP BY {cols} HAVING count(*) > 1)"
            ).fetchone()[0]
            resultados.append(Resultado(cid, f"chave({cols})", dup == 0, f"chaves repetidas={dup}"))

        for regra in objeto.get("quality", []) or []:
            resultados.append(_avaliar_qualidade(con, cid, tabela, regra, None, fisica))
    return resultados


def verificar_todos(
    ids: set[str] | None = None, projeto: Projeto | None = None, corte: str | None = None
) -> list[Resultado]:
    """Verifica os contratos (todos, ou só os de `ids`) contra o motor do projeto, no corte, se dado."""
    projeto = projeto or atual()
    con = duckdb.connect(str(projeto.warehouse), read_only=True)
    resultados = []
    try:
        for cid, (_, contrato) in sorted(contratos.todos(projeto).items()):
            if ids is not None and cid not in ids:
                continue
            if contratos.propriedade_custom(contrato, "manualProduction"):
                continue  # produzida fora do processo (nível 1): a tabela não está no motor
            resultados.extend(verificar(contrato, con, corte))
    finally:
        con.close()
    return resultados
