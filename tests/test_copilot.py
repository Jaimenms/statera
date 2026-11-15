"""The copilot (statera.copilot), without a model: the tests script what the model would reply and check what
the agent does with it: what it offers the model, what it lets through, what it sends back, what it may run."""

import json
import textwrap
from pathlib import Path

import duckdb
import pytest

from statera import cli, launcher
from statera.consume import catalog
from statera.copilot import console, knowledge, llm
from statera.copilot import sql as sql_
from statera.copilot.agent import CHOICES, Agent

GOLD = """
kind: DataContract
id: gold-sample-by-uf
version: 1.0.0
description: {purpose: Records and value by UF and reference month, limitations: none}
customProperties:
  - {property: layer, value: gold}
  - property: classification
    value: {sharing: broad, nature: aggregated, secrecy: none}
schema:
  - name: sample_by_uf
    physicalName: gold.sample_by_uf
    dataGranularityDescription: One row per reference month and UF
    properties:
      - {name: period, logicalType: string, primaryKey: true}
      - {name: uf, logicalType: string, primaryKey: true, description: UF abbreviation}
      - {name: uf_name, logicalType: string, description: UF name}
      - {name: records, logicalType: integer, description: Number of records}
      - {name: cut_date, logicalType: date}
"""

PEOPLE = """
kind: DataContract
id: silver-people
version: 1.0.0
description: {purpose: One person per row}
customProperties:
  - {property: layer, value: silver}
  - property: classification
    value: {sharing: restricted, nature: personal, secrecy: other}
schema:
  - name: people
    physicalName: silver.people
    properties:
      - {name: person_id, logicalType: string}
      - {name: name, logicalType: string, tags: [personal-data]}
      - {name: city, logicalType: string}
"""

QUERY = """
id: org.example.query.by-uf
title: Series by UF
description: Monthly records of one UF
theme: test
bases: [gold.sample_by_uf]
frequency: on-demand
purpose: study
parameters:
  - {name: uf, type: uf, required: true}
  - {name: since, type: period}
limitations: none
output_classification: {sharing: broad, nature: aggregated, secrecy: statistical}
owner: team-x
status: cataloged
version: 0.1.0
"""

METRICS = """
metrics:
  - name: total
    label: Total records
    description: Sum of the records
    type: simple
    aggregation: sum
    expression: records
    time: period
    sources:
      - {table: gold.sample_by_uf, dimensions: [uf]}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def repository(minimal_project):
    """The minimal project with an aggregated gold table, a personal one, a query, a metric and data."""
    root = minimal_project.root
    _write(root / "products/sample/5-process/contracts/sample_by_uf.odcs.yaml", GOLD)
    _write(root / "products/sample/5-process/contracts/people.odcs.yaml", PEOPLE)
    _write(root / "queries/test/by-uf/query.yaml", QUERY)
    _write(root / "queries/test/by-uf/by_uf.sql", "select 1")
    _write(root / "products/sample/5-process/metrics.yaml", METRICS)
    minimal_project.warehouse.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(minimal_project.warehouse))
    con.execute("create schema gold; create schema silver")
    con.execute(
        "create table gold.sample_by_uf as select * from (values "
        "('2026-01', 'SP', 'São Paulo', 10, date '2026-02-20'), ('2026-02', 'SP', 'São Paulo', 7, date '2026-03-20'), "
        "('2026-01', 'RJ', 'Rio de Janeiro', 4, date '2026-02-20')) t(period, uf, uf_name, records, cut_date)"
    )
    con.execute(
        "create table silver.people as select 'p' || i as person_id, 'Name ' || i as name, "
        "case when i <= 5 then 'Recife' else 'Olinda' end as city from range(1, 7) r(i)"
    )
    con.close()
    return minimal_project


class Scripted:
    """A model that replies what the test wrote, in order, and keeps what it was shown and held to."""

    def __init__(self, *replies: str):
        self.replies, self.shown, self.schemas = list(replies), [], []

    def chat(self, messages: list[dict], schema: dict) -> str:
        self.shown.append({m["role"]: m["content"] for m in messages})
        self.schemas.append(schema["properties"])
        return self.replies.pop(0)


def choose(kind: str, *cards: str, say: str = "") -> str:
    return json.dumps({"thought": "because", "kind": kind, "cards": list(cards), "say": say})


def fill(**fields) -> str:
    return json.dumps({"thought": "because", "assumptions": [], **fields})


def agent_with(repository, *replies: str) -> tuple[Agent, Scripted]:
    cat = catalog.open(repository)
    model = Scripted(*replies)
    return Agent(model, knowledge.build(cli.parser(), repository, cat), cat), model


SUM_SP = "select sum(records) as records from gold.sample_by_uf where uf = 'SP' and period like '2026-%'"


# --- what the model is shown --------------------------------------------------------------------------


def test_cards_come_from_the_repository_and_the_command_line(repository):
    cards = knowledge.build(cli.parser(), repository, catalog.open(repository))
    ids = {c.id for c in cards.cards}
    assert {"command:query", "query:by-uf", "metric:total", "table:gold.sample_by_uf", "product:sample"} <= ids
    assert "command:copilot" not in ids  # the copilot does not propose itself
    assert "table:bronze.records" not in ids  # a contract without a description is skipped, not fatal
    table = cards.get("table:gold.sample_by_uf").detail()
    assert "grain: One row per reference month and UF" in table  # what one row is
    assert "count(*) counts groups" in table and "query:by-uf" in table and "metric:total" in table
    assert "--param" in cards.get("query:by-uf").detail()
    assert "--filter" in cards.get("command:metric").detail()  # the command's real options
    assert cards.get("query:by-uf").line.startswith("query:by-uf — Series by UF. Monthly records of one UF")


def test_search_and_lenient_ids(repository):
    cards = knowledge.build(cli.parser(), repository, catalog.open(repository))
    assert cards.search("séries por UF")[0].id == "query:by-uf"  # accents and plurals do not get in the way
    assert {c.kind for c in cards.search("records by uf", kinds=("table",))} == {"table"}
    assert cards.search("xyzzy") == []
    assert cards.get("gold.sample_by_uf").id == "table:gold.sample_by_uf"  # the kind may be left out
    assert cards.get("table:sample_by_uf").id == "table:gold.sample_by_uf"  # and the schema
    assert cards.get("nothing") is None


def test_outside_a_repository_only_the_commands():
    cards = knowledge.build(cli.parser())
    assert {c.kind for c in cards.cards} == {"command"}
    assert cards.search("creates a data repository")[0].id == "command:init"
    agent = Agent(model := Scripted(choose("command", "command:init"), fill(options={"folder": "my-org"})), cards)
    assert agent.ask("start a repository in my-org").text == "statera init my-org"
    assert model.schemas[0]["kind"]["enum"] == ["command", "answer", "ask"]  # no data here: nothing else to choose


# --- 1. choose --------------------------------------------------------------------------------------------


def test_the_model_chooses_among_cards_that_exist(repository):
    agent, model = agent_with(repository, choose("ask", say="Which UF?"))
    assert agent.ask("the series").text == "Which UF?"
    offered = model.schemas[0]
    assert offered["kind"]["enum"] == ["query", "metric", "sql", "command", "answer", "ask"]  # small: no search
    assert {"query:by-uf", "metric:total", "table:gold.sample_by_uf", "command:status", "product:sample"} <= set(
        offered["cards"]["items"]["enum"]
    )  # an id that is not in the repository cannot be written
    assert "query:by-uf — Series by UF" in model.shown[0]["system"] and "search " not in model.shown[0]["system"]
    assert model.shown[0]["user"] == "THE REQUEST: the series\n(Write in the language of the request.)"


def test_a_large_repository_is_searched_and_the_model_may_search_again(repository, monkeypatch):
    monkeypatch.setattr(knowledge, "LISTED", 2)  # this repository has more than that: too big to list
    agent, model = agent_with(repository, choose("search", say="people city"), choose("sql", "table:silver.people"),
                              fill(sql="select city, count(*) as people from silver.people group by city"))  # fmt: skip
    outcome = agent.ask("records by UF")
    first, second = (s["cards"]["items"]["enum"] for s in model.schemas[:2])
    assert "search" in model.schemas[0]["kind"]["enum"] and "search   none of the cards" in model.shown[0]["system"]
    assert "table:gold.sample_by_uf" in first and "table:silver.people" not in first  # found by the request's words
    assert "table:silver.people" in second and "command:status" in first  # the commands are always offered
    assert "You searched 'people city': the cards found are listed" in model.shown[1]["user"]
    assert outcome.kind == "sql" and [s.action for s in outcome.steps] == ["search", "choose"]


def test_a_choice_without_its_card_or_a_reply_that_is_not_json_is_made_again(repository):
    agent, model = agent_with(repository, choose("query"), "sure! here you go", choose("command", "command:status"),
                              fill(options={"product": "", "period": "", "cut-date": ""}))  # fmt: skip
    assert agent.ask("where are the editions?").text == "statera status"
    assert "You chose query without a card of that kind" in model.shown[1]["user"]


def test_the_model_that_never_gets_there_is_stopped(repository):
    agent, model = agent_with(repository, *["not json"] * CHOICES)
    outcome = agent.ask("anything")
    assert outcome.kind == "none" and model.replies == []
    assert agent.conversation == ["Person: anything"]


# --- 2. fill, 3. check ----------------------------------------------------------------------------------


def test_a_cataloged_query_only_takes_the_parameters_it_declares(repository):
    agent, model = agent_with(repository, choose("query", "query:by-uf"),
                              fill(answers="yes", parameters={"uf": "SP", "since": ""}, assumptions=["São Paulo is SP"]))  # fmt: skip
    outcome = agent.ask("series of São Paulo")
    assert outcome.argv == ["query", "run", "by-uf", "--param", "uf=SP"] and outcome.assumptions == ["São Paulo is SP"]
    assert outcome.text == "statera query run by-uf --param uf=SP"
    parameters = model.schemas[1]["parameters"]  # built from query.yaml: `year` cannot be written
    assert list(parameters["properties"]) == ["uf", "since"] and parameters["required"] == ["uf"]
    assert parameters["additionalProperties"] is False
    assert "Parameters (--param name=value)" in model.shown[1]["system"]  # the card in full, only now


def test_a_parameter_of_the_wrong_type_goes_back_to_the_model(repository):
    agent, model = agent_with(repository, choose("query", "query:by-uf"), fill(answers="yes", parameters={"uf": "São Paulo"}),
                              fill(answers="yes", parameters={"uf": "SP"}))  # fmt: skip
    outcome = agent.ask("series of São Paulo")
    assert "Refused: Parameter uf='São Paulo' is not of type uf" in model.shown[2]["user"]
    assert outcome.text == "statera query run by-uf --param uf=SP"
    assert outcome.steps[1].refused.startswith("Parameter uf=")  # shown to whoever watches


def test_a_query_that_does_not_return_what_was_asked_is_set_aside(repository):
    agent, model = agent_with(repository, choose("query", "query:by-uf"), fill(answers="no", parameters={"uf": "SP"}),
                              choose("sql", "table:gold.sample_by_uf"), fill(sql=SUM_SP))  # fmt: skip
    outcome = agent.ask("how many records did São Paulo have in 2026?")
    assert "query:by-uf" not in model.schemas[2]["cards"]["items"]["enum"]  # not offered again
    assert "You chose query:by-uf and it did not work: it does not return what was asked" in model.shown[2]["user"]
    assert outcome.kind == "sql" and sql_.run(outcome.checked, agent.catalog)["records"].tolist() == [17]


def test_a_metric_takes_the_dimensions_its_sources_allow(repository):
    agent, model = agent_with(repository, choose("metric", "metric:total"),
                              fill(by=["uf"], period="", **{"from": "2026-01", "to": "2026-12"}, filters={"uf": "SP"}))  # fmt: skip
    outcome = agent.ask("total by UF in 2026, only SP")
    assert outcome.text == "statera metric total --by uf --from 2026-01 --to 2026-12 --filter uf=SP"
    assert model.schemas[1]["by"]["items"]["enum"] == ["uf"] and list(model.schemas[1]["filters"]["properties"]) == [
        "uf"
    ]


def test_a_command_takes_the_options_the_command_line_has(repository):
    run = {"product": "sample", "period": "2026-01", "cut-date": "", "with-dependencies": False, "force": False}
    agent, model = agent_with(repository, choose("command", "command:run"), fill(options={**run, "approve": True}),
                              json.dumps({"thought": "it sounds related", "asked": "no"}))  # fmt: skip
    outcome = agent.ask("run january of sample")
    assert outcome.argv == ["run", "--product", "sample", "--period", "2026-01"]  # the flag was not asked for
    options = model.schemas[1]["options"][
        "properties"
    ]  # from the parser: an option that is not there cannot be written
    assert list(options) == ["product", "period", "cut-date", "with-dependencies", "force", "approve"]
    assert options["approve"] == {"type": "boolean"} and options["period"] == {"type": "string"}
    assert "--approve, which means: simulates the human tasks (demo)" in model.shown[2]["system"]
    assert outcome.steps[-1].refused == "left out: the request does not ask for it"
    assert "product:sample — [dissemination] Sample product" in model.shown[1]["system"]  # the products that exist

    agent, model = agent_with(repository, choose("command", "command:validate"),
                              fill(options={"level": "", "no-dbt": False, "verbose": True}),
                              json.dumps({"thought": "they said: list everything", "asked": "yes"}))  # fmt: skip
    assert agent.ask("validate and list everything that passed").text == "statera validate --verbose"
    assert model.schemas[1]["options"]["properties"]["level"]["enum"] == ["1", "2", "3", "4", ""]  # a choice, or none


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"product": "nothing", "period": "", "cut-date": ""}, "Unit 'nothing'"),
        ({"product": "", "period": "2026-01", "cut-date": ""}, "give --product with --period"),
    ],
)
def test_a_command_the_command_line_would_refuse_goes_back_to_the_model(repository, options, reason):
    agent, model = agent_with(repository, choose("command", "command:status"), fill(options=options),
                              fill(options={"product": "sample", "period": "", "cut-date": ""}))  # fmt: skip
    outcome = agent.ask("where is it?")
    assert reason in model.shown[2]["user"] and "Fix exactly that and reply again" in model.shown[2]["user"]
    assert outcome.text == "statera status --product sample"  # the person only sees the one that is valid


def test_counting_rows_of_an_aggregated_table_is_questioned_once(repository):
    count = "select count(*) as n from gold.sample_by_uf where uf = 'SP' and period like '2026-%'"
    agent, model = agent_with(repository, choose("sql", "table:gold.sample_by_uf"), fill(sql=count),
                              fill(sql=count.replace("count(*)", "sum(records)")))  # fmt: skip
    outcome = agent.ask("how many records did SP have in 2026?")
    refusal = model.shown[2]["user"]
    assert "count(*) counts those rows" in refusal and "its measures are records" in refusal
    assert "grain: One row per reference month and UF" in model.shown[1]["system"]  # the table's card, in full
    assert "SUM(records)" in outcome.text and sql_.run(outcome.checked, agent.catalog)["n"].tolist() == [17]

    agent, _ = agent_with(repository, choose("sql", "table:gold.sample_by_uf"), fill(sql=count), fill(sql=count))
    assert "COUNT(*)" in agent.ask("in how many months did SP have records?").text  # sent again: it stands


def test_sql_is_sent_back_with_what_the_engine_and_the_data_say(repository):
    agent, model = agent_with(
        repository,
        choose("sql", "table:gold.sample_by_uf"),
        fill(sql="select sum(total) from gold.sample_by_uf"),  # no such column
        fill(sql="select sum(records) as records from gold.sample_by_uf where uf = 'São Paulo'"),  # no such value
        fill(sql="select sum(records) as records from gold.sample_by_uf where uf = 'SP'"),
    )
    outcome = agent.ask("how many records did São Paulo have?")
    assert "the engine rejected it" in model.shown[2]["user"] and "total" in model.shown[2]["user"]
    told = model.shown[3]["user"]  # the name is in another column: the code that goes with it is given
    assert "No row of gold.sample_by_uf has uf = 'São Paulo'" in told and "uf_name = 'São Paulo' has uf = 'SP'" in told
    assert outcome.checked.explained and sql_.run(outcome.checked, agent.catalog)["records"].tolist() == [17]


def test_a_table_used_without_its_card_is_shown_before_the_sql_stands(repository):
    people = "select city, count(*) as people from silver.people group by city"
    agent, model = agent_with(repository, choose("sql", "table:gold.sample_by_uf"), fill(sql=people), fill(sql=people))
    outcome = agent.ask("people by city")
    assert "silver.people" not in model.shown[1]["system"] and "purpose: One person per row" in model.shown[2]["system"]
    assert "you used table:silver.people without its card" in model.shown[2]["user"]
    assert outcome.checked.tables == ["silver.people"] and "restricted · personal" in outcome.checked.cautions[0]


def test_an_explanation_is_made_from_the_cards_chosen(repository):
    agent, model = agent_with(repository, choose("answer", "product:sample", "command:status"),
                              json.dumps({"thought": "because", "say": "Sample product is a dissemination product."}))  # fmt: skip
    outcome = agent.ask("what is the sample product?")
    assert outcome.kind == "answer" and outcome.text == "Sample product is a dissemination product."
    assert "using ONLY what the cards below say" in model.shown[1]["system"]
    assert "product sample — Sample product" in model.shown[1]["system"]


def test_the_next_request_sees_the_conversation_in_a_few_lines(repository):
    agent, model = agent_with(repository, choose("sql", "table:gold.sample_by_uf"), fill(sql=SUM_SP),
                              choose("ask", say="By month of which year?"))  # fmt: skip
    agent.ask("how many records did SP have in 2026?")
    agent.note("the SQL ran: 1 row(s). First rows:\n records\n      17" + "x" * 2000)
    agent.ask("and by month?")
    seen = model.shown[-1]["user"]
    assert seen.startswith("THE CONVERSATION SO FAR (the request may refine it):\nPerson: how many records did SP")
    assert "Copilot (sql): SELECT SUM(records) AS records FROM gold.sample_by_uf" in seen
    assert "Then: the SQL ran: 1 row(s)" in seen and "x" * 800 not in seen  # what ran, cut short
    assert seen.endswith("THE REQUEST: and by month?\n(Write in the language of the request.)")


# --- ad hoc SQL -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("delete from gold.sample_by_uf", "only a SELECT"),
        ("drop table gold.sample_by_uf", "only a SELECT"),
        ("select 1 from gold.sample_by_uf; select 2 from gold.sample_by_uf", "exactly one SELECT"),
        ("select * from read_csv('/etc/passwd')", "read only the cataloged tables"),
        ("select read_text('/etc/passwd') from gold.sample_by_uf", "read_text() is not allowed"),
        ("select * from sample_by_uf", "did you mean gold.sample_by_uf?"),
        ("select * from gold.nothing", "is not in the catalog"),
        ("select * from information_schema.tables", "is not in the catalog"),
        ("select 1", "reads no table"),
        ("select from where", "does not parse"),
        ("select nothing from gold.sample_by_uf", "the engine rejected it"),
    ],
)
def test_sql_that_is_not_one_read_of_cataloged_tables_is_refused(repository, sql, reason):
    with pytest.raises(sql_.SqlError, match=reason.replace("(", r"\(").replace(")", r"\)").replace("?", r"\?")):
        sql_.check(sql, catalog.open(repository))


def test_sql_with_a_cte_and_the_classification_caution(repository):
    cat = catalog.open(repository)
    checked = sql_.check(
        "with sp as (select * from gold.sample_by_uf where uf = 'SP') select sum(records) from sp;", cat
    )
    assert checked.tables == ["gold.sample_by_uf"] and checked.cautions == [] and checked.doubts == []
    guarded = sql_.check("select city, count(*) as people from silver.people group by city", cat)
    assert "restricted · personal" in guarded.cautions[0] and guarded.doubts == []  # not aggregated: count is right


def test_sql_without_an_engine_is_checked_as_far_as_possible(repository):
    repository.warehouse.unlink()
    cat = catalog.open(repository)
    checked = sql_.check("select sum(records) from gold.sample_by_uf where uf = 'XX'", cat)
    assert not checked.explained and sql_.unknown_values(checked, cat) == []  # nothing to ask the data
    with pytest.raises(sql_.SqlError, match="only a SELECT"):
        sql_.check("delete from gold.sample_by_uf", cat)


def test_internal_product_needs_an_authorization(repository):
    _write(
        repository.root / "products/base-data/4-collect/contracts/records.odcs.yaml",
        GOLD.replace("gold-sample-by-uf", "bronze-records").replace("gold.sample_by_uf", "bronze.records"),
    )
    with pytest.raises(sql_.SqlError, match="no valid authorization"):
        sql_.check("select * from bronze.records", catalog.open(repository, role="someone-else"))


def test_values_a_query_filters_by_must_exist(repository):
    cat = catalog.open(repository)

    def unknown(sql: str) -> list[str]:
        return sql_.unknown_values(sql_.check(sql, cat), cat)

    assert (
        unknown("select sum(records) from gold.sample_by_uf t where t.uf in ('SP', 'RJ') and period like '2026-%'")
        == []
    )
    (problem,) = unknown("select sum(records) from gold.sample_by_uf where uf = 'XX'")
    assert problem.startswith("No row of gold.sample_by_uf has uf = 'XX'") and "has the values: 'SP', 'RJ'" in problem
    (problem,) = unknown("select sum(records) from gold.sample_by_uf where 'Rio' = uf")
    assert "uf_name = 'Rio de Janeiro' has uf = 'RJ'" in problem  # found through the name
    assert unknown("select count(*) from silver.people where name = 'Nobody'") == []  # personal: never looked at


def test_values_of_a_column(repository):
    cat = catalog.open(repository)
    assert sql_.values("gold.sample_by_uf.uf", cat).endswith("has the values: 'SP', 'RJ'")
    assert "containing 'rj': 'RJ'" in sql_.values("gold.sample_by_uf.uf rj", cat)
    assert "INCOMPLETE: 3 distinct values" in sql_.values("gold.sample_by_uf.records", cat, limit=2)
    assert "has the values: 'Recife'" in sql_.values("silver.people.city", cat)  # Olinda: one row, not shown
    assert "no value shared by at least 5 rows" in sql_.values("silver.people.person_id", cat)
    for request, reason in [("silver.people.name", "personal data"), ("gold.sample_by_uf.nothing", "has no column"),
                            ("gold.nothing.uf", "not in the catalog"), ("uf", "schema.table.column")]:  # fmt: skip
        with pytest.raises(sql_.SqlError, match=reason):
            sql_.values(request, cat)


# --- what the copilot may run ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "who"),
    [
        ("query run by-uf --param uf=SP", console.READ),
        ("metric total --period 2026-01", console.READ),
        ("status", console.READ),
        ("validate", console.READ),
        ("rules check", console.READ),
        ("rules sync", console.CHANGE),
        ("run --product sample --period 2026-01", console.CHANGE),
        ("sync", console.CHANGE),
        ("new-product x", console.CHANGE),
        ("resume --product sample --period 2026-01", console.PERSON),  # a person's decision
        ("run --product sample --period 2026-01 --approve", console.PERSON),  # simulates the person
        ("run --product sample --period 2026-01 --force", console.PERSON),  # republishes
        ("clean", console.PERSON),
        ("demo", console.PERSON),
    ],
)
def test_who_may_run_each_command(command, who):
    assert console.policy(command.split()) == who


STATUS = (choose("command", "command:status"), fill(options={"product": "", "period": "", "cut-date": ""}))
RUN = (
    choose("command", "command:run"),
    fill(options={"product": "sample", "period": "2026-01", "cut-date": "", "with-dependencies": False, "force": False,
                  "approve": False}),
)  # fmt: skip
RESUME = (
    choose("command", "command:resume"),
    fill(options={"product": "sample", "period": "2026-01", "cut-date": "", "reject": False, "approve": False,
                  "acknowledge-alerts": ""}),
)  # fmt: skip


def _console(repository, monkeypatch, *replies, **options):
    agent, _ = agent_with(repository, *replies)
    ran = []
    monkeypatch.setattr(console, "execute", lambda argv: ran.append(argv) or (0, "printed"))
    return console.Console(agent, interactive=False, **options), ran


def test_without_a_terminal_only_what_reads_runs_and_only_with_yes(repository, monkeypatch, capsys):
    c, ran = _console(repository, monkeypatch, *STATUS, yes=False)
    assert c.run("where are the editions?") == 0 and ran == []
    assert "$ statera status" in capsys.readouterr().out  # shown, not run

    c, ran = _console(repository, monkeypatch, *STATUS, *RUN, *RESUME, yes=True)
    c.turn("where are the editions?")
    c.turn("run january")
    c.turn("approve it")
    out = capsys.readouterr().out
    assert ran == [["status"]]  # --yes covers what only reads
    assert "it changes things" in out and "yours to run" in out
    notes = [line for line in c.agent.conversation if line.startswith("Then:")]  # kept for the next request
    assert "`statera status` ran and ended with code 0. It printed:\nprinted" in notes[0]
    assert "it was not run" in notes[1] and "the copilot does not run it" in notes[2]


def test_a_yes_in_the_terminal_runs_what_changes_and_never_a_persons_decision(repository, monkeypatch):
    c, ran = _console(repository, monkeypatch, *RUN, *RESUME, *RUN)
    c.interactive = True
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    c.turn("run january")
    c.turn("approve it")
    assert ran == [["run", "--product", "sample", "--period", "2026-01"]]
    monkeypatch.setattr("builtins.input", lambda prompt: "")  # just Enter: no, for what changes
    c.turn("again")
    assert len(ran) == 1


def _typing(monkeypatch, *lines: str) -> None:
    """What the person types at the terminal, in order."""
    typed = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt: next(typed))


def test_the_person_edits_the_command_and_the_edit_is_checked(repository, monkeypatch, capsys):
    c, ran = _console(repository, monkeypatch, *STATUS, *STATUS, *STATUS)
    c.interactive = True
    _typing(monkeypatch, "e", "statera status --product sample", "y")
    c.turn("where are the editions?")
    assert ran == [["status", "--product", "sample"]]  # what the person wrote, not what the model did
    assert "$ statera status --product sample" in capsys.readouterr().out  # shown again before the question
    assert "Then: the person changed what was proposed to: statera status --product sample" in c.agent.conversation

    _typing(monkeypatch, "e", "status --nothing", "status --product nobody", "", "n")  # refused twice, given up
    c.turn("again")
    out = capsys.readouterr().out
    assert "✘ statera: error: unrecognized arguments: --nothing" in out and "nobody" in out.split("✘")[2]
    assert len(ran) == 1

    _typing(monkeypatch, "e", "resume --product sample --period 2026-01")  # an edit does not change who may run
    c.turn("and again")
    assert "yours to run" in capsys.readouterr().out and len(ran) == 1


def test_the_person_edits_the_sql_and_the_edit_is_checked(repository, monkeypatch, capsys):
    c, _ = _console(repository, monkeypatch, choose("sql", "table:gold.sample_by_uf"), fill(sql=SUM_SP))
    c.interactive = True
    _typing(monkeypatch, "e", "delete from gold.sample_by_uf", "select count(*) as n from gold.sample_by_uf where uf = 'XX'", "y")  # fmt: skip
    c.turn("how many records did São Paulo have?")
    out = capsys.readouterr().out
    assert "✘ only a SELECT is allowed" in out
    cautions = [line for line in out.splitlines() if line.startswith("  ! ")]  # the person decides, knowing
    assert any("'XX'" in line for line in cautions) and any("aggregated" in line for line in cautions)
    assert out.endswith("\n n\n 0\n")  # it ran, as the person wrote it
    assert "the SQL ran: 1 row(s)" in c.agent.conversation[-1]


def test_a_proposal_is_explained_from_the_cards_of_what_it_names(repository, monkeypatch, capsys):
    query = (choose("query", "query:by-uf"), fill(answers="yes", parameters={"uf": "SP", "since": ""}))
    c, ran = _console(repository, monkeypatch, *query, json.dumps({"say": "It reads the series of SP.\nuf=SP: the UF."}),
                      choose("sql", "table:gold.sample_by_uf"), fill(sql=SUM_SP), "not json")  # fmt: skip
    c.interactive = True
    _typing(monkeypatch, "?", "n")
    c.turn("série de São Paulo")
    out = capsys.readouterr().out
    assert "  It reads the series of SP.\n  uf=SP: the UF.\n" in out and ran == []  # explained, asked again
    shown = c.agent.model.shown[-1]
    assert "THE COMMAND:\nstatera query run by-uf --param uf=SP\n" in shown["system"]
    assert "Monthly records of one UF" in shown["system"]  # the card of the query, with the card of the command
    assert "THE REQUEST: série de São Paulo" in shown["user"] and list(c.agent.model.schemas[-1]) == ["language", "say"]

    _typing(monkeypatch, "?", "n")
    c.turn("and how many in all?")
    assert "I could not explain it" in capsys.readouterr().out  # the model said nothing usable
    assert "THE SQL QUERY:\nSELECT" in c.agent.model.shown[-1]["system"]
    assert "One row per reference month and UF" in c.agent.model.shown[-1]["system"]  # the card of the table


def test_sql_answer_runs_and_is_written(repository, monkeypatch, capsys, tmp_path):
    c, _ = _console(repository, monkeypatch, choose("sql", "table:gold.sample_by_uf"),
                    fill(sql=SUM_SP.replace("sum(records)", "sum(records) * 1.0"), assumptions=["SP is São Paulo"]),
                    yes=True, output=str(tmp_path / "out"))  # fmt: skip
    assert c.run("how many records did São Paulo have?") == 0
    out = capsys.readouterr().out
    assert "assumed: SP is São Paulo" in out and "reads: gold.sample_by_uf" in out
    assert out.split("\n records\n")[1].split()[0] == "17"  # 17, not 17.0
    assert (tmp_path / "out" / "result.csv").read_text() == "records\n17\n"
    assert "SUM(records)" in (tmp_path / "out" / "query.sql").read_text()
    assert "the SQL ran: 1 row(s)" in c.agent.conversation[-1]


def test_the_steps_are_shown_as_they_happen(repository, capsys):
    cat = catalog.open(repository)
    model = Scripted(choose("query", "query:by-uf"), fill(answers="yes", parameters={"uf": "sp"}),
                     fill(answers="yes", parameters={"uf": "SP"}))  # fmt: skip
    Agent(model, knowledge.build(cli.parser(), repository, cat), cat, on_step=console.trace(verbose=True)).ask("x")
    out = capsys.readouterr().out
    assert "  · choose: query:by-uf\n      because\n" in out
    assert '  · query: {"uf": "sp"}\n      because\n      ✘ Parameter uf=\'sp\' is not of type uf' in out


def test_command_line_of_what_is_run(repository, monkeypatch):
    assert console.command_line(["status"])[1:] == ["-m", "statera.cli", "status"]  # this environment
    root = repository.root
    (root / "pyproject.toml").write_text("[dependency-groups]\nproduce = []\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "in_repository_environment", lambda r: True)
    monkeypatch.setattr(console.shutil, "which", lambda name: "/bin/uv")
    assert console.command_line(["status"])[1:] == ["-m", "statera.cli", "status"]  # reading: nothing to add
    assert console.command_line(["run", "--product", "x"])[:6] == ["/bin/uv", "run", "--quiet", "--project", str(root), "--all-groups"]  # fmt: skip


# --- the model is local, and optional -----------------------------------------------------------------


def test_the_model_must_be_on_this_computer():
    for url in ("http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"):
        assert llm.is_local(url)
    for url in ("https://api.example.com", "http://192.168.0.10:11434", "http://ollama.internal:11434"):
        with pytest.raises(llm.CopilotError, match="only talks to a model on this computer"):
            llm.Ollama(url=url)


def test_settings_argument_environment_repository_default(monkeypatch):
    monkeypatch.delenv(llm.ENV_MODEL, raising=False)
    monkeypatch.delenv(llm.ENV_URL, raising=False)
    assert llm.settings() == {"model": llm.DEFAULT_MODEL, "url": llm.DEFAULT_URL, "context": llm.DEFAULT_CONTEXT}
    config = {"model": "from-toml", "context": 16384}
    assert llm.settings(config)["model"] == "from-toml" and llm.settings(config)["context"] == 16384
    monkeypatch.setenv(llm.ENV_MODEL, "from-env")
    assert (
        llm.settings(config)["model"] == "from-env"
        and llm.settings(config, "from-argument")["model"] == "from-argument"
    )


def test_no_ollama_says_what_to_do(monkeypatch):
    monkeypatch.setenv(llm.ENV_URL, "http://127.0.0.1:9")  # nothing listens there
    model = llm.Ollama(**llm.settings())
    assert "Ollama is not running" in model.problem() and "ollama serve" in model.problem()
    monkeypatch.setattr(model, "installed", lambda: ["other:7b"])
    assert f"ollama pull {llm.DEFAULT_MODEL}" in model.problem()
    monkeypatch.setattr(model, "installed", lambda: [llm.DEFAULT_MODEL])
    assert model.problem() is None


def test_command_without_ollama_or_without_the_extra(repository, monkeypatch, capsys):
    monkeypatch.setenv(llm.ENV_URL, "http://127.0.0.1:9")
    assert cli.main(["copilot", "hello"]) == 1
    assert "Ollama is not running" in capsys.readouterr().err

    def missing(args):
        raise ModuleNotFoundError("No module named 'httpx'", name="httpx")

    monkeypatch.setattr(cli, "_cmd_copilot", missing)
    assert cli.main(["copilot", "hello"]) == 1
    assert 'copilot = ["statera[copilot]"]' in capsys.readouterr().err


def test_launcher_adds_the_copilot_group_only_when_the_repository_declares_it(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[dependency-groups]\ncopilot = ["statera[copilot]"]\n', encoding="utf-8")
    assert launcher.uv_groups(["copilot", "hello"], tmp_path) == ["--group", "copilot"]
    (tmp_path / "pyproject.toml").write_text("[dependency-groups]\nproduce = []\n", encoding="utf-8")
    assert launcher.uv_groups(["copilot"], tmp_path) == []  # the command then says how to declare it
    assert launcher.uv_groups(["check"], tmp_path) == ["--all-groups"]
