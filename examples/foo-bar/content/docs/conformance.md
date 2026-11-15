# Conformidade

> ARQUIVO GERADO pela plataforma Statera. Não edite à mão: a próxima geração desfaz a mudança.  
> Gerado por `statera docs`. Fontes: metadados do repositório (contratos, produtos, processos, regras, séries).  
> Para mudar: edite as fontes e rode `statera docs`.  
> **Exemplo com DADOS SINTÉTICOS.**

Níveis de conformidade da especificação (1 catalogado, 2 reprodutível, 3 controlado, 4 avaliado). Cada produto declara o nível-alvo no `product.odps.yaml`; o CI exige o alvo.

| Produto | Alvo | Atingido | Alvo cumprido |
|---|---|---|---|
| bar | 3 | 3 | sim |
| baz | 2 | 2 | sim |
| foo | 3 | 3 | sim |

## Pendências para o próximo nível

- **bar**, nível 4: 8-evaluate/indicators.yaml declara que não se aplica (a definir: indicadores de qualidade da edição e plano de ação); o nível pede indicadores de qualidade
- **baz**, nível 3: sem controles (6-analyse/controls.yaml ou dbt-tests/)
- **baz**, nível 3: 2-design/processes/to-be.yaml declara que não se aplica (a desenhar: hoje o script SPSS roda à mão; o processo to-be vem com o nível 3); o nível pede processo executável
- **foo**, nível 4: 8-evaluate/indicators.yaml declara que não se aplica (a definir: indicadores de serviço da extração do Sistema Foo (atraso, completude)); o nível pede indicadores de serviço: atraso, completude
