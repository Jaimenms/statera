"""The agent: choose what answers, fill it in, check it. Small decisions, each one constrained and checked.

A small local model cannot be trusted with one big open decision, but it is good at small closed ones. So
a turn is a few short calls, each with only what that decision needs and a JSON schema the server holds the
reply to:

    1. CHOOSE   the request and one line per card -> what answers it: a cataloged query, a metric, a
                command, tables to write SQL over, cards to explain from, or a question back. The cards are an
                `enum` in the schema: the model cannot name one that does not exist. In a repository too
                big to list, the candidates are retrieved by the words of the request, and the model may
                ask for another search with words of its own.
    2. FILL     only the chosen card(s) in full -> the parameters of the query, the options of the metric
                or of the command, the SQL, or the explanation. For a query, a metric and a command the
                schema is built from what they declare (the command's, from the command line's own
                parser): an undeclared parameter, dimension or option cannot be written. The model also
                says whether the query really returns what was asked (if not, the choice is made again
                without it), and a flag it turned on is kept only if, asked again, the request asks for it.
    3. CHECK    the command against the real command line and the catalog; the SQL against sql.py (one
                read-only SELECT, cataloged tables, the engine's EXPLAIN, values that exist). What fails
                goes back to the model with the reason, a few times; the person only sees what passed.

Every call starts from short instructions plus the conversation so far in a few lines (what was asked,
what was answered, what ran and what it printed), so "and by month?" refines the previous answer and the
context never outgrows the model.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

from statera.copilot import sql as sql_
from statera.copilot.knowledge import DATA, Card, Knowledge

CANDIDATES = 15  # data cards offered to the choice when the repository is too big to list
ATTEMPTS = 3  # times the model may try to fill a choice in before the turn gives up on it
CHOICES = 4  # times the choice may be made in one turn (another search, a query that does not answer)
REMEMBERED = 6  # lines of the conversation carried into each call
LINE = 700  # characters of each of those lines

ROLE = """You are the copilot of Statera, a framework for repositories of statistical data products. \
A person asks in plain language and gets back a `statera` command or a SQL query to run. Reply with one \
JSON object and nothing else. Write `thought`, `say` and `assumptions` in the language of the request.
"""

CHOOSE = (
    ROLE
    + """
Your task now: choose WHAT answers the request. Do not answer it yet.

  {{"thought": "<one sentence>", "kind": "<kind>", "cards": ["<card id>"], "say": ""}}

kind:
  query    the person wants exactly what ONE cataloged query returns. cards: that query.
  metric   the person wants an official metric. cards: that metric.
  sql      the person wants data that no query or metric returns exactly. cards: the tables needed \
(prefer gold tables; ref tables translate codes into names).
  command  the request is about operating the repository (run or approve an edition, see where it is, \
validate, generate documentation, see what a change affects…), not about numbers. cards: that command.
  answer   the person asks what something is or how it works, and wants neither data nor an action. cards: \
the cards that explain it.
  ask      the request is too vague to choose. say: your question to the person.
{search}
Rules:
- A cataloged query returns what its title says and filters only by its parameters. When that is what the \
person wants, choose the query: cataloged queries are reviewed and tested. When the person wants another \
filter (a year, a category), another grouping ("by region"), a total or another measure, no query returns \
exactly that: choose sql.
- Choose `ask` only when you cannot tell what is wanted; a detail you can reasonably assume is not a reason.

CARDS:
{cards}
"""
)
SEARCH = "  search   none of the cards below fits, and other words might find one. say: those words.\n"

QUERY = (
    ROLE
    + """
Your task now: this cataloged query was chosen for the request. Say whether it returns what was asked, and \
with which parameters.

  {{"thought": "<one sentence>", "answers": "yes" or "no", "parameters": {{...}}, "assumptions": []}}

- answers: "yes" only if the query, run with these parameters, returns what was asked. It filters ONLY by \
its parameters; it cannot filter by anything else, group differently or total. Otherwise "no".
- parameters: the values in the format the card gives. When the person names something and the parameter \
expects a code, give the code of what was NAMED.
- assumptions: every interpretation you made, one short sentence each.

THE QUERY:
{card}
"""
)

METRIC = (
    ROLE
    + """
Your task now: this official metric was chosen for the request. Give its options.

  {{"thought": "<one sentence>", "by": [], "period": "", "from": "", "to": "", "filters": {{}}, \
"assumptions": []}}

- by: the dimensions to break the metric down by, only when the person asks "by …".
- period: one reference period, YYYY-MM. Or from and to, YYYY-MM each, for a range (a whole year is \
from YYYY-01 to YYYY-12). Leave empty what does not apply.
- filters: dimension -> value, only for what the person restricted.
- assumptions: every interpretation you made, one short sentence each.

THE METRIC:
{card}
"""
)

COMMAND = (
    ROLE
    + """
Your task now: give the options of this command of the `statera` command line, for the request.

  {{"thought": "<one sentence>", "options": {{...}}, "assumptions": []}}

- options: one entry per option of the command. Give a value ONLY to what the request needs and leave \
every other one empty ("" for a text, false for a flag, [] for a list).
- A flag is true only when the person asked for what that flag does; when in doubt, false.
- Take the values (product, period) from the request; never invent them. A period is YYYY-MM.
- assumptions: every interpretation you made, one short sentence each.

THE COMMAND:
{card}
THE PRODUCTS of this repository:
{products}
"""
)

EXPLAIN = (
    ROLE
    + """
Your task now: explain what the person asks, using ONLY what the cards below say.

  {{"thought": "<one sentence>", "say": "<the explanation>"}}

- Say nothing the cards do not say. When they do not explain it, say so, and name the command below (if \
any) that would show it.

THE CARDS:
{card}
"""
)

FLAG = (
    ROLE
    + """
Your task now: a command was written for the request with the optional flag --{flag}, which means: {meaning}

  {{"thought": "<one sentence>", "asked": "yes" or "no"}}

- asked: "yes" only if the request itself asks for what this flag does. A flag that merely sounds related \
to the request, or that might be convenient, is "no". Without the flag the command does its normal job.
"""
)

SQL = (
    ROLE
    + """
Your task now: write ONE {dialect} SELECT that answers the request, over these tables only.

  {{"thought": "<one sentence>", "sql": "SELECT …", "assumptions": []}}

- Write the tables as schema.table and use only the columns listed in their cards.
- Mind what one row is (the "grain"). In an aggregated table a row is a group, not an individual: \
"how many X" is sum(<the column that counts X>), never count(*).
- Filter exactly what was asked (the year, the category), follow every "note:" of the cards, and give \
every output column a clear name.
- assumptions: every interpretation you made (which table, what the year means, which values count), one \
short sentence each.

THE TABLES:
{card}
"""
)


class Invalid(ValueError):
    """What the model proposed cannot be used; the message goes back to it."""


class Unfit(Invalid):
    """The card chosen does not answer the request: the choice is made again, without it."""


@dataclass
class Step:
    """One thing the agent did, for whoever watches (the terminal shows them as they happen)."""

    action: str  # choose | search | query | metric | command | sql | answer
    input: str
    thought: str = ""
    refused: str = ""  # why it was sent back to the model, when it was


@dataclass
class Outcome:
    kind: str  # command | sql | ask | answer | none (the model did not get to an answer)
    text: str  # the command line, the SQL, the question or the explanation
    thought: str = ""
    assumptions: list[str] = field(default_factory=list)
    argv: list[str] | None = None  # kind == "command": the arguments after `statera`
    checked: sql_.Checked | None = None  # kind == "sql"
    steps: list[Step] = field(default_factory=list)


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}


class Agent:
    def __init__(self, model, knowledge: Knowledge, catalog=None, on_step: Callable[[Step], None] | None = None):
        self.model, self.knowledge, self.catalog, self.on_step = model, knowledge, catalog, on_step
        self.conversation: list[str] = []  # what was asked, answered and run, a line each
        self.steps: list[Step] = []

    def ask(self, question: str) -> Outcome:
        """One turn: the person's request (or reply) in, an outcome out."""
        self.steps = []
        outcome = self._turn(question)
        outcome.steps = self.steps
        self.conversation.append(f"Person: {question}")
        if outcome.kind != "none":
            self.conversation.append(f"Copilot ({outcome.kind}): {' '.join(outcome.text.split())}")
        return outcome

    def note(self, text: str) -> None:
        """Records what happened after a turn (what ran, what it printed), for the next request."""
        self.conversation.append(f"Then: {text}")

    # --- 1. choose -------------------------------------------------------------------------------------

    def _turn(self, question: str) -> Outcome:
        candidates = self._candidates(question)
        notes: list[str] = []  # what this turn already tried, for the next choice
        for _ in range(CHOICES):
            search = not self.knowledge.complete
            kinds = [k for k in ("query", "metric", "sql", "command") if self._of(candidates, k)]
            kinds += ["answer", "ask", *(["search"] if search else [])]
            reply = self._call(
                CHOOSE.format(search=SEARCH if search else "", cards="\n".join(c.line for c in candidates)),
                question,
                {
                    "thought": TEXT,
                    "kind": {"type": "string", "enum": kinds},
                    "cards": {"type": "array", "items": {"type": "string", "enum": [c.id for c in candidates]}},
                    "say": TEXT,
                },
                notes,
            )
            if reply is None:
                continue
            kind, say, thought = reply.get("kind"), str(reply.get("say") or "").strip(), str(reply.get("thought", ""))
            if kind == "ask" and say:
                return Outcome("ask", say, thought)
            if kind == "search" and say:
                found = [c for c in self.knowledge.search(say, CANDIDATES, DATA) if c not in candidates]
                self._step("search", say, thought, "" if found else "nothing new matches those words")
                candidates = found + candidates
                notes.append(f"You searched {say!r}: {'the cards found are listed' if found else 'nothing new'}.")
                continue
            chosen = [c for c in self._of(candidates, kind) if c.id in (reply.get("cards") or [])]
            if not chosen:
                notes.append(f"You chose {kind} without a card of that kind: choose again, with its card(s).")
                continue
            chosen = chosen if kind in ("sql", "answer") else chosen[:1]
            self._step("choose", ", ".join(c.id for c in chosen), thought)
            fill = {"query": self._query, "metric": self._metric, "command": self._command, "sql": self._sql,
                    "answer": self._explain}[kind]  # fmt: skip
            try:
                return fill(question, chosen)
            except Invalid as error:  # this choice led nowhere: choose again, knowing why
                if kind not in ("sql", "answer"):
                    candidates = [c for c in candidates if c not in chosen]
                notes.append(f"You chose {', '.join(c.id for c in chosen)} and it did not work: {error}")
        return Outcome("none", "")

    def _candidates(self, question: str) -> list[Card]:
        """The cards offered to the choice: every command and product and, of the data, all of it (a
        repository small enough to list) or what the words of the request and of the conversation find."""
        commands = [c for c in self.knowledge.cards if c.kind in ("command", "product")]
        data = [c for c in self.knowledge.cards if c.kind in DATA]
        if not self.knowledge.complete:
            data = self.knowledge.search(" ".join([question, *self.conversation[-2:]]), CANDIDATES, DATA)
        return data + commands

    @staticmethod
    def _of(cards: list[Card], kind: str) -> list[Card]:
        """The cards a kind of answer is made from: any card explains; SQL is written over tables."""
        return [c for c in cards if kind == "answer" or c.kind == ("table" if kind == "sql" else kind)]

    def _explain(self, question: str, chosen: list[Card]) -> Outcome:
        def check(reply: dict) -> Outcome:
            if not str(reply.get("say") or "").strip():
                raise Invalid("say the explanation")
            return Outcome("answer", str(reply["say"]).strip(), str(reply.get("thought", "")))

        system = EXPLAIN.format(card="\n\n".join(c.detail() for c in chosen[:4]))
        return self._fill("answer", system, question, {"thought": TEXT, "say": TEXT}, check)

    # --- 2. fill, 3. check -----------------------------------------------------------------------------

    def _query(self, question: str, chosen: list[Card]) -> Outcome:
        from statera.consume import queries

        name = chosen[0].id.partition(":")[2]
        declared = queries.get(name, self.catalog.project).meta.get("parameters", [])
        properties = {
            "thought": TEXT,
            "answers": {"type": "string", "enum": ["yes", "no"]},
            "parameters": {
                "type": "object",
                "properties": {p["name"]: TEXT for p in declared},
                "required": [p["name"] for p in declared if p.get("required")],
                "additionalProperties": False,
            },
            "assumptions": TEXTS,
        }

        def check(reply: dict) -> Outcome:
            if reply.get("answers") != "yes":
                raise Unfit(f"it does not return what was asked ({reply.get('thought', '')}). Choose sql")
            argv = ["query", "run", name]
            for parameter, value in (reply.get("parameters") or {}).items():
                if str(value).strip():
                    argv += ["--param", f"{parameter}={str(value).strip()}"]
            return self._checked_command(argv, reply)

        return self._fill("query", QUERY.format(card=chosen[0].detail()), question, properties, check)

    def _metric(self, question: str, chosen: list[Card]) -> Outcome:
        from statera.consume import metrics

        name = chosen[0].id.partition(":")[2]
        definitions = metrics.definitions(self.catalog.project)
        metric = definitions[name]
        parts = (
            [definitions[metric[k]] for k in ("numerator", "denominator")] if metric["type"] == "ratio" else [metric]
        )
        dimensions = sorted({d for m in parts for s in m.get("sources", []) for d in s["dimensions"]})
        properties = {
            "thought": TEXT,
            "by": {"type": "array", "items": {"type": "string", "enum": dimensions}} if dimensions else TEXTS,
            "period": TEXT,
            "from": TEXT,
            "to": TEXT,
            "filters": {"type": "object", "properties": dict.fromkeys(dimensions, TEXT)},
            "assumptions": TEXTS,
        }

        def check(reply: dict) -> Outcome:
            argv = ["metric", name]
            if by := _strings(reply.get("by")):
                argv += ["--by", ",".join(by)]
            for option in ("period", "from", "to"):
                if value := str(reply.get(option) or "").strip():
                    argv += [f"--{option}", value]
            for dimension, value in (reply.get("filters") or {}).items():
                if str(value).strip():
                    argv += ["--filter", f"{dimension}={str(value).strip()}"]
            return self._checked_command(argv, reply)

        return self._fill("metric", METRIC.format(card=chosen[0].detail()), question, properties, check)

    def _command(self, question: str, chosen: list[Card]) -> Outcome:
        name = chosen[0].id.partition(":")[2]
        products = "\n".join(c.line for c in self.knowledge.cards if c.kind == "product") or "(none)"
        options, arguments, flags = _options(name)

        def check(reply: dict) -> Outcome:
            given = dict(reply.get("options") or {})
            for flag in [k for k, v in given.items() if v is True]:  # a flag has to be asked for, not just plausible
                given[flag] = self._asked_for(question, flag, flags[flag])
            return self._checked_command([name, *arguments(given)], reply)

        system = COMMAND.format(card=chosen[0].detail(), products=products)
        properties = {"thought": TEXT, "options": options, "assumptions": TEXTS}
        return self._fill("command", system, question, properties, check)

    def _asked_for(self, question: str, flag: str, meaning: str) -> bool:
        """A second, narrow look at a flag the model turned on: small models turn on whatever sounds
        related. It stays on only when the person asked for what it does."""
        system = FLAG.format(flag=flag, meaning=meaning)
        reply = self._call(system, question, {"thought": TEXT, "asked": {"type": "string", "enum": ["yes", "no"]}}, [])
        kept = bool(reply) and reply.get("asked") == "yes"
        if not kept:
            self._step(
                "command",
                f"--{flag}",
                str((reply or {}).get("thought", "")),
                "left out: the request does not ask for it",
            )
        return kept

    def _sql(self, question: str, chosen: list[Card]) -> Outcome:
        cards = {c.id: c for c in chosen}
        doubted: set[str] = set()

        def system() -> str:
            return SQL.format(card="\n\n".join(c.detail() for c in cards.values()), dialect=sql_.dialect(self.catalog))

        def check(reply: dict) -> Outcome:
            written = str(reply.get("sql", ""))
            try:
                checked = sql_.check(written, self.catalog)
            except sql_.SqlError as error:
                raise Invalid(str(error)) from error
            if unseen := [f"table:{t}" for t in checked.tables if f"table:{t}" not in cards]:
                cards.update({c.id: c for c in map(self.knowledge.get, unseen) if c})  # shown from now on
                raise Invalid(f"you used {', '.join(unseen)} without its card. It is listed now: read it, write again")
            problems = sql_.unknown_values(checked, self.catalog)
            if checked.doubts and written not in doubted:  # valid, probably not what was meant: asked once
                doubted.add(written)
                problems += [*checked.doubts, "If it is what you mean, send the same SQL again."]
            if problems:
                raise Invalid(" ".join(problems))
            thought, assumptions = str(reply.get("thought", "")), _strings(reply.get("assumptions"))
            return Outcome("sql", checked.sql, thought, assumptions, checked=checked)

        return self._fill("sql", system, question, {"thought": TEXT, "sql": TEXT, "assumptions": TEXTS}, check)

    def _fill(self, action: str, system, question: str, properties: dict, check: Callable[[dict], Outcome]) -> Outcome:
        """Asks the model to fill the choice in and checks the reply; what fails goes back to it with the
        reason. `system` may be a function, when a refusal changes what the model should see."""
        refusals: list[str] = []
        for _ in range(ATTEMPTS):
            reply = self._call(system() if callable(system) else system, question, properties, refusals)
            if reply is None:
                continue
            try:
                return check(reply)
            except Invalid as error:
                proposed = reply.get("sql") or json.dumps(reply.get("options") or reply.get("parameters") or {})
                self._step(action, " ".join(proposed.split()), str(reply.get("thought", "")), str(error))
                if isinstance(error, Unfit):
                    raise
                refusals.append(f"You proposed: {proposed}\nRefused: {error}\nFix exactly that and reply again.")
        raise Invalid(refusals[-1] if refusals else "the model did not reply with the JSON asked for")

    def _checked_command(self, argv: list[str], reply: dict) -> Outcome:
        """The command, after the real command line and the catalog accepted it."""
        from statera import cli

        errors = io.StringIO()
        try:
            with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                args = cli.parse(argv)
        except SystemExit:
            raise Invalid((errors.getvalue().strip().splitlines() or ["invalid command"])[-1]) from None
        self._exists(args)
        thought, assumptions = str(reply.get("thought", "")), _strings(reply.get("assumptions"))
        return Outcome("command", "statera " + shlex.join(argv), thought, assumptions, argv=argv)

    def _exists(self, args) -> None:
        """What the command names must exist: the query and its parameters, the metric and its dimensions,
        the product."""
        from statera.cli import _pairs
        from statera.consume import metrics, queries
        from statera.project import ProjectError

        if self.catalog is None:
            return
        project = self.catalog.project
        try:
            if args.command == "query" and args.id:
                query = queries.get(args.id, project)
                if args.action in ("run", "show"):
                    queries.validate_parameters(query, _pairs(args.param))
            elif args.command == "metric":
                definitions = metrics.definitions(project)
                if args.name not in definitions:
                    raise metrics.MetricError(f"there is no metric {args.name!r}; metrics: {', '.join(definitions)}")
                metric = definitions[args.name]
                ratio = metric["type"] == "ratio"
                for part in [definitions[metric[k]] for k in ("numerator", "denominator")] if ratio else [metric]:
                    metrics._simple_sql(part, [d for d in (args.by or "").split(",") if d], None, _pairs(args.filter_))
            elif getattr(args, "product", None):
                project.product(args.product)
        except (ValueError, ProjectError) as error:  # QueryError and MetricError are ValueErrors
            raise Invalid(str(error)) from error

    # --- the model -------------------------------------------------------------------------------------

    def _call(self, system: str, question: str, properties: dict, more: list[str]) -> dict | None:
        """One call: the task, the conversation so far in a few lines, the request, and what this turn
        already tried. None when the reply is not the JSON object asked for."""
        remembered = [line if len(line) <= LINE else line[:LINE] + "…" for line in self.conversation[-REMEMBERED:]]
        user = ""
        if remembered:
            user += "THE CONVERSATION SO FAR (the request may refine it):\n" + "\n".join(remembered) + "\n\n"
        user += f"THE REQUEST: {question}\n(Write in the language of the request.)"
        if more:
            user += "\n\n" + "\n\n".join(more[-2:])
        schema = {"type": "object", "properties": properties, "required": list(properties)}
        reply = self.model.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], schema)
        try:
            data = json.loads(reply)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _step(self, action: str, text: str, thought: str = "", refused: str = "") -> None:
        self.steps.append(Step(action, text, thought, refused))
        if self.on_step:
            self.on_step(self.steps[-1])


def _options(command: str) -> tuple[dict, Callable[[dict], list[str]], dict[str, str]]:
    """The options of a command as a JSON schema (one property per option, from the command line's own
    parser: a flag is a boolean, a choice is an enum), the function that turns a reply into arguments, and
    what each flag means. So the model decides each option that exists, and cannot write one that does not."""
    from statera import cli

    subcommands = next(a for a in cli.parser()._actions if isinstance(a, argparse._SubParsersAction))
    actions = [a for a in subcommands.choices[command]._actions if not isinstance(a, argparse._HelpAction)]
    names = {a: a.option_strings[-1].lstrip("-") if a.option_strings else a.dest for a in actions}
    properties = {}
    for action, name in names.items():
        if isinstance(action, argparse._StoreTrueAction):
            properties[name] = {"type": "boolean"}
        elif isinstance(action, argparse._AppendAction):
            properties[name] = TEXTS
        elif action.choices:
            optional = bool(action.option_strings) or action.nargs == "?"
            properties[name] = {"type": "string", "enum": [*map(str, action.choices), *([""] if optional else [])]}
        else:
            properties[name] = TEXT

    def arguments(reply: dict) -> list[str]:
        argv = []
        for action in sorted(actions, key=lambda a: bool(a.option_strings)):  # positional arguments first
            value = reply.get(names[action])
            flag = action.option_strings[-1:]  # empty for a positional argument
            if isinstance(action, argparse._StoreTrueAction):
                argv += flag if value is True else []
            elif isinstance(action, argparse._AppendAction):
                argv += [part for item in _strings(value) for part in (*flag, item)]
            elif str(value or "").strip():
                argv += [*flag, str(value).strip()]
        return argv

    flags = {n: a.help or "" for a, n in names.items() if isinstance(a, argparse._StoreTrueAction)}
    return {"type": "object", "properties": properties, "required": list(properties)}, arguments, flags


def _strings(values) -> list[str]:
    return [str(v) for v in values or [] if str(v).strip()]
