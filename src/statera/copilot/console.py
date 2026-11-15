"""The copilot in the terminal: shows what it found and how, asks before running, runs.

What the copilot may run, by what the command does (`policy`):

    read     only reads (queries, metrics, status, validation…) and SQL, which is always read-only:
             runs after a yes; `--yes` gives that yes in advance.
    change   produces or changes something (run, sync, docs, new-product…): runs after a yes, asked every
             time, whatever `--yes` says; never without a terminal to ask in.
    person   records a person's decision (resume, --approve) or deletes (clean, demo, --force): never run
             by the copilot. It shows the command; the person runs it.

Without a terminal (a script, a pipe) nothing that changes runs, and what reads runs only with `--yes`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from statera import launcher
from statera.copilot import sql as sql_
from statera.copilot.agent import Agent, Outcome, Step
from statera.copilot.knowledge import build
from statera.copilot.llm import CopilotError, Ollama, settings

READ, CHANGE, PERSON = "read", "change", "person"
READ_COMMANDS = launcher.CONSUMPTION_COMMANDS | {"validate", "conformance", "contracts", "lineage"}
READ_ACTIONS = {"rules": {"check"}, "dbt": {"check"}, "bpmn": {"check"}}
PERSON_COMMANDS = {"resume", "clean", "demo"}
PERSON_OPTIONS = {"--approve", "--force", "--registry"}
ROWS_SHOWN = 50
BYE = {"exit", "quit", "sair", ":q"}


def policy(argv: list[str]) -> str:
    """Who may run the command: see the module docstring."""
    if argv[0] in PERSON_COMMANDS or PERSON_OPTIONS & set(argv):
        return PERSON
    if argv[0] in READ_COMMANDS or (len(argv) > 1 and argv[1] in READ_ACTIONS.get(argv[0], ())):
        return READ
    return CHANGE


def command_line(argv: list[str]) -> list[str]:
    """How to run `statera <argv>` from here: this environment, or, for a command that needs the
    repository's production tools, the one uv prepares (the same choice the global launcher makes)."""
    root, uv = launcher.repository_root(), shutil.which("uv")
    if root and uv and launcher.in_repository_environment(root) and launcher.uv_groups(argv, root):
        return launcher.pinned_command(uv, root, argv)
    return [sys.executable, "-m", "statera.cli", *argv]


def execute(argv: list[str]) -> tuple[int, str]:
    """Runs the command, showing what it prints as it goes; returns the exit code and the output."""
    env = {**os.environ, launcher.ENV_HANDOFF: "1", "PYTHONUNBUFFERED": "1"}
    lines = []
    with subprocess.Popen(
        command_line(argv), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
    ) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
    return process.returncode, "".join(lines)


class Console:
    def __init__(self, agent: Agent, yes: bool = False, output: str | None = None, interactive: bool | None = None):
        self.agent, self.yes, self.output = agent, yes, output
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    # --- one turn --------------------------------------------------------------------------------------

    def turn(self, question: str) -> Outcome:
        outcome = self.agent.ask(question)
        if outcome.kind == "command":
            self._command(outcome)
        elif outcome.kind == "sql":
            self._sql(outcome)
        elif outcome.kind == "ask":
            print(f"\n? {outcome.text}")
        elif outcome.kind == "answer":
            print(f"\n{outcome.text}")
        else:
            print("\nI could not get to an answer I can stand behind. Say it another way, or with more detail.")
        return outcome

    def _explain(self, outcome: Outcome) -> None:
        if outcome.thought:
            print(f"  {outcome.thought}")
        for assumption in outcome.assumptions:
            print(f"  · assumed: {assumption}")

    def _command(self, outcome: Outcome) -> None:
        print(f"\n  $ {outcome.text}")
        self._explain(outcome)
        kind = policy(outcome.argv)
        if kind == PERSON:
            print("  This one is yours to run: it records a person's decision or deletes data; the copilot does not.")
            self.agent.note(f"the person was shown `{outcome.text}`; the copilot does not run it")
            return
        if not self._agreed(kind, "It changes the repository or the data. Run it?" if kind == CHANGE else "Run it?"):
            self.agent.note(f"the person was shown `{outcome.text}` and it was not run")
            return
        print()
        code, output = execute(outcome.argv)
        self.agent.note(f"`{outcome.text}` ran and ended with code {code}. It printed:\n{output}")

    def _sql(self, outcome: Outcome) -> None:
        checked = outcome.checked
        print("\n" + "\n".join(f"  {line}" for line in checked.sql.splitlines()))
        self._explain(outcome)
        print(f"  · reads: {', '.join(checked.tables)} (ad hoc SQL: no cataloged query or metric answers this)")
        for caution in checked.cautions:
            print(f"  ! {caution}")
        if not checked.explained:
            print("  There is no data to read here, so this was not checked against the engine (statera doctor).")
            self.agent.note("the SQL was shown; there is no engine here to run it")
            return
        if not self._agreed(READ, "Run it?"):
            self.agent.note("the SQL was shown and not run")
            return
        try:
            df = sql_.run(checked, self.agent.catalog)
        except Exception as error:  # noqa: BLE001 (the engine's message is what helps)
            print(f"\n  The engine failed: {str(error).strip().splitlines()[0]}")
            self.agent.note(f"the SQL failed when run: {error}")
            return
        for column in df.select_dtypes("float").columns:  # a sum of integers comes back as a float: 412, not 412.0
            if df[column].dropna().map(float.is_integer).all():
                df[column] = df[column].astype("Int64")
        print("\n" + df.head(ROWS_SHOWN).to_string(index=False))
        if len(df) > ROWS_SHOWN:
            print(f"… {len(df)} rows; the first {ROWS_SHOWN} shown (--output <folder> writes them all)")
        if df.empty:
            print("No rows: a filter may name a value the data does not have. Tell me what to change.")
        if self.output:
            folder = Path(self.output)
            folder.mkdir(parents=True, exist_ok=True)
            df.to_csv(folder / "result.csv", index=False, lineterminator="\n")
            (folder / "query.sql").write_text(checked.sql + "\n", encoding="utf-8")
            print(f"written: {folder / 'result.csv'}, {folder / 'query.sql'}")
        self.agent.note(f"the SQL ran: {len(df)} row(s). First rows:\n{df.head(10).to_string(index=False)}")

    def _agreed(self, kind: str, question: str) -> bool:
        if kind == READ and self.yes:
            return True
        if not self.interactive:
            reason = "--yes runs what only reads." if kind == READ else "it changes things; run it yourself."
            print(f"  Not run: {reason}")
            return False
        default = kind == READ
        try:
            reply = input(f"  {question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return default if not reply else reply in ("y", "yes", "s", "sim")

    # --- the conversation ------------------------------------------------------------------------------

    def run(self, question: str = "") -> int:
        """With a question: answers it (and whatever the copilot asks back). Without: a conversation."""
        once = bool(question)
        if not once:
            print("Ask in plain language; an empty line or `exit` ends. Each answer can be refined.")
        while True:
            if not question:
                try:
                    question = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if not question or question.lower() in BYE:
                    return 0
            try:
                outcome = self.turn(question)
            except KeyboardInterrupt:
                print("\n(interrupted)")
                outcome = None
            question = ""
            if once and (outcome is None or outcome.kind != "ask" or not self.interactive):
                return 0 if outcome is not None and outcome.kind != "none" else 1


def trace(verbose: bool):
    """What the copilot did, one line per step, as it goes: what it chose, and what was sent back to it."""

    def show(step: Step) -> None:
        text = " ".join(step.input.split())
        print(f"  · {step.action}: {text if len(text) <= 110 else text[:109] + '…'}", flush=True)
        if verbose and step.thought:
            print(f"      {step.thought}", flush=True)
        if step.refused:
            reason = " ".join(step.refused.split())
            print(f"      ✘ {reason if len(reason) <= 300 else reason[:299] + '…'}", flush=True)

    return show


def main(args) -> int:
    from statera import cli
    from statera.consume import catalog
    from statera.project import ProjectError, current

    try:
        project = current()
    except ProjectError:
        project = None  # outside a data repository: the copilot still knows the commands (init…)
    try:
        chosen = settings(project.config.get("copilot") if project else None, args.model)
        model = Ollama(**chosen)
        if problem := model.problem():
            raise CopilotError(problem)
        cat = catalog.open(project, engine=args.engine) if project else None
        agent = Agent(model, build(cli.parser(), project, cat), cat, on_step=trace(args.verbose))
        print(f"[statera copilot] {model.model}, on this computer ({model.url})", file=sys.stderr)
        return Console(agent, yes=args.yes, output=args.output).run(" ".join(args.question).strip())
    except CopilotError as error:
        print(f"[statera] {error}", file=sys.stderr)
        return 1
