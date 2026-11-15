"""Testes da plataforma: sem depender de nenhum repositório de conteúdo real.

O projeto mínimo abaixo tem um produto com um processo to-be que exercita o executor:
tarefa automática, gateway com condições, caminho de exceção, tarefa humana, reprovação e
tarefa semiautomática. E uma pequena malha de produtos: o produto interno de fonte `dados-base`
libera cortes, e o produto de divulgação `consumidor` espera o corte num evento intermediário.
"""

import textwrap

import pytest

PROCESSO = """
id: Processo_Teste
name: Processo de teste
lanes:
  - {id: L1, name: Pipeline, role: equipe-x}
  - {id: L2, name: Equipe, role: equipe-x}
elements:
  - {id: Inicio, type: startEvent, name: Início, lane: L1}
  - {id: T_A, type: serviceTask, name: A, lane: L1, statera: {type: automatic, gsbpm: '4.3', durationMin: 1, implementation: 'py:etapas_de_teste.registrar'}}
  - {id: T_B, type: serviceTask, name: B, lane: L1, statera: {type: automatic, gsbpm: '6.2', durationMin: 1, implementation: 'py:etapas_de_teste.decidir'}}
  - {id: G, type: exclusiveGateway, name: 'OK?', lane: L1}
  - {id: T_X, type: userTask, name: Investigar, lane: L2, statera: {type: manual, gsbpm: '5.3', durationMin: 60, exception: true}}
  - {id: T_H, type: userTask, name: Aprovar, lane: L2, statera: {type: manual, gsbpm: '6.5', durationMin: 30}}
  - {id: G2, type: exclusiveGateway, name: 'Aprovado?', lane: L2}
  - {id: T_P, type: userTask, name: Publicar, lane: L2, statera: {type: semi-automatic, gsbpm: '7.3', durationMin: 10, implementation: 'py:etapas_de_teste.publicar'}}
  - {id: Fim, type: endEvent, name: Fim, lane: L2}
flows:
  - {id: F1, from: Inicio, to: T_A}
  - {id: F2, from: T_A, to: T_B}
  - {id: F3, from: T_B, to: G}
  - {id: F4, from: G, to: T_H, name: sim, condition: ok}
  - {id: F5, from: G, to: T_X, name: não, condition: failed}
  - {id: F6, from: T_X, to: T_B}
  - {id: F7, from: T_H, to: G2}
  - {id: F8, from: G2, to: T_P, name: sim, condition: approved}
  - {id: F9, from: G2, to: T_H, name: não, condition: rejected}
  - {id: F10, from: T_P, to: Fim}
"""

# Produto interno de fonte: extrai (falso) e marca o corte (falso: só grava o manifesto).
PROCESSO_FONTE = """
id: Processo_Fonte
name: Corte dos dados base
lanes:
  - {id: L1, name: Pipeline, role: equipe-x}
elements:
  - {id: Inicio, type: startEvent, name: Data de corte, lane: L1, event: timer}
  - {id: T_Extrair, type: serviceTask, name: Extrair, lane: L1, statera: {type: automatic, gsbpm: '4.3', durationMin: 1, implementation: 'py:etapas_de_teste.extrair'}}
  - {id: T_Marcar, type: serviceTask, name: Marcar o corte, lane: L1, statera: {type: automatic, gsbpm: '7.3', durationMin: 1, implementation: 'py:etapas_de_teste.marcar_corte'}}
  - {id: Fim, type: endEvent, name: Corte disponível, lane: L1}
flows:
  - {id: F1, from: Inicio, to: T_Extrair}
  - {id: F2, from: T_Extrair, to: T_Marcar}
  - {id: F3, from: T_Marcar, to: Fim}
"""

# Consumidor: espera o corte do produto interno (evento de mensagem) e calcula.
PROCESSO_CONSUMIDOR = """
id: Processo_Consumidor
name: Edição do consumidor
lanes:
  - {id: L1, name: Pipeline, role: equipe-x}
elements:
  - {id: Inicio, type: startEvent, name: Início, lane: L1}
  - {id: Ev_Corte, type: intermediateCatchEvent, name: Corte disponível, lane: L1, event: message, statera: {awaits: 'py:statera.collect.pin_cut'}}
  - {id: T_Calcular, type: serviceTask, name: Calcular, lane: L1, statera: {type: automatic, gsbpm: '5.1', durationMin: 1, implementation: 'py:etapas_de_teste.registrar'}}
  - {id: Fim, type: endEvent, name: Fim, lane: L1}
flows:
  - {id: F1, from: Inicio, to: Ev_Corte}
  - {id: F2, from: Ev_Corte, to: T_Calcular}
  - {id: F3, from: T_Calcular, to: Fim}
"""


@pytest.fixture
def projeto_minimo(tmp_path, monkeypatch):
    raiz = tmp_path / "organization"
    arquivos = {
        "statera.toml": """
            [project]
            name = "orgao-exemplo"
            organization = "Órgão de exemplo"
            acronym = "ex"
            namespace = "br.gov.exemplo"
        """,
        "governance/roles.yaml": """
            roles:
              - {id: equipe-x, name: Equipe X, org_unit: "000001", git_team: "@org/equipe-x"}
        """,
        "products/teste/1-specify/product.odps.yaml": """
            apiVersion: v1.0.0
            kind: DataProduct
            id: teste
            name: Produto de teste
            status: active
            customProperties:
              - {property: urn, value: br.gov.exemplo.teste}
        """,
        "products/teste/1-specify/calendar.yaml": """
            product: teste
            frequency: monthly
            editions:
              - {period: "2026-01", cut_date: "2026-02-20", release: "2026-02-27"}
        """,
        "products/dados-base/1-specify/product.odps.yaml": """
            apiVersion: v1.0.0
            kind: DataProduct
            id: dados-base
            name: Dados base (produto interno de fonte)
            status: active
            customProperties:
              - {property: urn, value: br.gov.exemplo.dados-base}
              - {property: alignment, value: source}
              - {property: visibility, value: internal}
              - property: authorizedConsumers
                value:
                  - {product: consumidor, purpose: teste}
                  - {role: equipe-x, purpose: study, until: "2099-12-31", act: Despacho 1}
            outputPorts:
              - {name: registros, version: 1.0.0, contractId: bronze-registros}
        """,
        "products/dados-base/4-collect/contracts/registros.odcs.yaml": """
            apiVersion: v3.1.0
            kind: DataContract
            id: bronze-registros
            name: Registros (bronze)
            version: 1.0.0
            status: active
            customProperties:
              - {property: layer, value: bronze}
            schema:
              - name: registros
                physicalName: bronze.registros
                properties:
                  - {name: id, logicalType: string}
        """,
        "products/dados-base/2-design/processes/to-be.yaml": PROCESSO_FONTE,
        "products/consumidor/1-specify/product.odps.yaml": """
            apiVersion: v1.0.0
            kind: DataProduct
            id: consumidor
            name: Consumidor dos dados base
            status: active
            customProperties:
              - {property: urn, value: br.gov.exemplo.consumidor}
            inputPorts:
              - {name: registros, version: 1.0.0, contractId: bronze-registros}
        """,
        "products/consumidor/1-specify/calendar.yaml": """
            product: consumidor
            frequency: monthly
            editions:
              - {period: "2026-01", cut_date: "2026-02-20", release: "2026-02-27"}
              - {period: "2026-02", cut_date: "2026-02-20", release: "2026-03-27"}
        """,
        "products/consumidor/2-design/processes/to-be.yaml": PROCESSO_CONSUMIDOR,
        "products/teste/2-design/processes/to-be.yaml": PROCESSO,
    }
    for rel, texto in arquivos.items():
        caminho = raiz / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(textwrap.dedent(texto).lstrip(), encoding="utf-8")
    monkeypatch.setenv("STATERA_PROJETO", str(raiz))
    monkeypatch.delenv("STATERA_DADOS", raising=False)
    monkeypatch.delenv("STATERA_REGISTRO", raising=False)
    monkeypatch.chdir(raiz)
    from statera import projeto

    return projeto.atual()
