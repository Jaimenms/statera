"""statera — plataforma do padrão de repositórios de produtos estatísticos (nome provisório).

A plataforma é genérica: não conhece nenhum produto. Tudo o que é próprio de um órgão (produtos
de divulgação e internos, referências, consultas, receitas) fica num repositório de CONTEÚDO, descrito por um
arquivo `statera.toml` na raiz. Ver `specification/` no repositório da plataforma.

Organização do pacote pelas fases do GSBPM 5.1:

    desenhar/     processos em YAML <-> BPMN 2.0 (fase 2)
    coletar/      extração datada para o bronze, dirigida pelos contratos (fase 4)
    processar/    projeto dbt gerado, regras de negócio -> unit tests (fase 5)
    analisar/     contratos ODCS, controles declarativos, controle de divulgação (fase 6)
    disseminar/   pacote da edição, Data Package, registro de edições (fase 7)
    avaliar/      indicadores de qualidade declarativos (fase 8)
    consumir/     biblioteca de consulta, métricas, consultas recorrentes, receitas
    execucao.py   executa o processo to-be do produto, tarefa a tarefa
"""

__version__ = "0.1.0"
ESPECIFICACAO = "0.1"
