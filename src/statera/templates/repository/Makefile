# Comandos do dia a dia do repositório de dados (rodam a Statera dentro de content/).
# `make` ou `make help` lista tudo.
CONTENT := content
STATERA := cd $(CONTENT) && uv run statera

.DEFAULT_GOAL := help
.PHONY: help setup sync validate lint conformance test test-all demo docs catalog new-product run resume status cuts queries recipes rules clean

help: ## Lista os comandos
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'

setup: ## Instala o ambiente (Python, Statera na versão fixada, dbt) e prepara os arquivos gerados
	cd $(CONTENT) && uv sync
	$(STATERA) sync

sync: ## Regenera os arquivos gerados (dbt_project.yml, calendários internos, BPMN, CODEOWNERS…)
	$(STATERA) sync

validate: ## Validação completa do repositório (o CI roda a mesma)
	$(STATERA) validate

lint: ## Estilo do código Python (ruff)
	cd $(CONTENT) && uv run ruff check . && uv run ruff format --check .

conformance: ## Nível de conformidade de cada produto e o que falta para o próximo
	$(STATERA) conformance

test: ## Testes rápidos
	cd $(CONTENT) && uv run pytest -q -m "not lento"

test-all: ## Todos os testes, inclusive o processo completo (mais lentos)
	cd $(CONTENT) && uv run pytest -q

demo: ## Demonstração do zero (só com dados sintéticos: apaga data/ e o registro)
	$(STATERA) demo

docs: ## Regenera a documentação em content/docs/
	$(STATERA) docs

catalog: ## Catálogo navegável com busca (abre content/target/static_index.html no navegador)
	$(STATERA) docs --interactive

new-product: ## Cria um produto: make new-product NAME=meu-produto [ALIGNMENT=consumer|aggregate|source]
	$(STATERA) new-product $(NAME) --alignment $(or $(ALIGNMENT),consumer)

run: ## Executa uma edição: make run PRODUCT=x PERIOD=2026-06
	$(STATERA) run --product $(PRODUCT) --period $(PERIOD) --with-dependencies

resume: ## Retoma depois de uma tarefa humana: make resume PRODUCT=x PERIOD=2026-06
	$(STATERA) resume --product $(PRODUCT) --period $(PERIOD)

status: ## Situação de uma edição: make status PRODUCT=x PERIOD=2026-06
	$(STATERA) status --product $(PRODUCT) --period $(PERIOD)

cuts: ## Cortes liberados pelos produtos internos
	$(STATERA) cuts

queries: ## Testa as consultas recorrentes
	$(STATERA) query test

recipes: ## Executa as receitas
	$(STATERA) recipes test

rules: ## Casos das regras em modelos Python
	$(STATERA) rules test

clean: ## Apaga dados e artefatos locais (não apaga o registro de edições)
	$(STATERA) clean
