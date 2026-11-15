# 03 — Classificação

Quatro dimensões independentes, para separar **quem pode receber** de **o que o dado contém**:

| Dimensão | Valores | Fonte |
|---|---|---|
| `sharing` (compartilhamento) | `broad` · `restricted` · `specific` | Decreto nº 10.046/2019 (níveis de compartilhamento) |
| `nature` (natureza) | `aggregated` · `deidentified` · `personal` · `sensitive-personal` | Lei nº 13.709/2018 (LGPD) |
| `secrecy` (sigilo) | `none` · `statistical` · `tax` · `other` | Legislação específica; Lei nº 12.527/2011 (LAI) |
| `disclosureControl` (controle de divulgação) | ausente; `cell-suppression` `{column, minimum, suppressedColumns, unpublishedColumns}` (agregados); ou `k-anonymity` `{k, quasiIdentifiers, suppressibleColumns, unpublishedColumns}` (microdados desidentificados) | GSBPM 6.4 |

## Onde fica

**No contrato** (perfil ODCS, 04):

```yaml
schema:
  - properties:
      - {name: saldo, classification: broad}
      - {name: estabelecimentos, classification: restricted}                # não publicada
      - {name: id_trabalhador, classification: specific, tags: [dado-pessoal]}
customProperties:
  - property: classification
    value:
      sharing: broad
      nature: aggregated
      secrecy: statistical
      disclosureControl: {method: cell-suppression, column: estabelecimentos, minimum: 3,
                          suppressedColumns: [admissoes, desligamentos, saldo], unpublishedColumns: [estabelecimentos]}
```

Na coluna, `classification` é o nível de compartilhamento; dado pessoal leva a etiqueta
`dado-pessoal` ou `dado-pessoal-sensivel`.

**Na aprovação** (`<unidade>/1-specify/classification.yaml`, mantido pelo encarregado de dados):
compartilhamento, natureza, sigilo, método de controle e base legal de cada ativo da unidade
(`assets`, cada um com `contract`, `sharing`, `nature`, `secrecy`, `disclosure_control` e `legal_basis`;
no topo, `unit`, `approved_by` e `reviewed_on`). O CI
**deve** exigir que contrato e aprovação coincidam: quem escreve o contrato não aprova sozinho a
própria classificação.

## Coerência (verificada sem dados)

- Coluna com dado pessoal sensível → tabela `sensitive-personal`; com dado pessoal → `personal` ou mais.
- Tabela `broad` → toda coluna `broad`, exceto as listadas em `unpublishedColumns`.

## Gate de publicação aberta

Uma tabela só vai para publicação aberta se: compartilhamento `broad`; natureza `aggregated` ou
`deidentified`; sigilo `none`, ou `statistical` **com** controle de divulgação declarado e
aplicado; e nenhuma coluna publicada for não ampla ou dado pessoal. Microdados `deidentified` só
saem com `k-anonymity`: desidentificado não é anonimizado (LGPD, art. 12), e combinações raras de
quase-identificadores (município, seção, sexo, faixas) podem reidentificar uma pessoa. O método aplica
supressão local, na ordem de `suppressibleColumns` (como o `localSuppression` do sdcMicro), e remove o
que continuar abaixo de k; o pacote registra os valores apagados e os registros removidos. A plataforma aplica o gate na
geração do pacote, nas consultas com saída ampla e, estaticamente, nos consumidores declarados
(`exposures` com `meta.sharing: broad`).

A classificação é metadado: a proteção de acesso real é do motor, com políticas geradas dos
contratos (recomendação da análise de escalabilidade, §5.3).
