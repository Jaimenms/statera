* Script SPSS LEGADO (ilustrativo): valor médio dos registros por UF e competência.
* Adaptação para rodar pelo repositório: só os caminhos de arquivo viraram FILE HANDLEs, que a
* plataforma define (registros = entrada; resultado = saída). O resto é a sintaxe original.
*   RN-BAZ-001  valor médio = valor total / número de registros, por UF e competência.

GET FILE=registros.
AGGREGATE OUTFILE=* /BREAK=competencia cod_uf /registros=N /valor_total=SUM(valor).
COMPUTE valor_medio = valor_total / registros.
FORMATS registros (F8.0) valor_total valor_medio (F12.2).
SORT CASES BY competencia cod_uf.
SAVE OUTFILE=resultado.
