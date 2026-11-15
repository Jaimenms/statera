# Script R LEGADO (ilustrativo): mediana do valor dos registros por UF e competência.
# Adaptação para rodar pelo repositório: só os caminhos de arquivo viraram as variáveis que a
# plataforma define no início (entradas$registros = entrada; saida = arquivo de saída). Só R base.
#   RN-BAZ-002  mediana do valor por UF e competência

registros <- readRDS(entradas$registros)
resultado <- aggregate(valor ~ competencia + cod_uf, data = registros, FUN = median)
names(resultado)[names(resultado) == "valor"] <- "valor_mediano"
resultado <- resultado[order(resultado$competencia, resultado$cod_uf), ]
saveRDS(resultado, saida)
