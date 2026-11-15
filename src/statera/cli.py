"""The `statera` command line. Run it from any folder of a data repository: it uses the Statera version
the repository pins (a global `statera` hands off to it; see launcher.py).

Getting started:
    statera init my-repo --name stats-example --namespace org.example --organization "..."
    statera doctor                       # checks the computer and the repository, says how to fix
    statera new-product my-statistic
Day to day:
    statera check                        # what CI runs: validation, lint and fast tests (--all: every test)
Production (the product's process):
    statera run --product foo --period 2026-06                    # stops at the 1st human task
    statera run --product foo --period 2026-06 --with-dependencies
    statera run --product bar-events --cut-date 2026-07-20         # internal product: one cut
    statera resume --product foo --period 2026-06                 # completes the task and continues
    statera status --product foo --period 2026-06
    statera tasks                                                 # the human tasks: who has to do what
    statera cuts                                                  # cuts released by the internal products
Quality and conformance:
    statera validate [--level 2]         statera conformance          statera contracts
Generated files:
    statera sync                         # dbt_project.yml, rule unit tests, BPMN, CODEOWNERS
Consumption (needs no production tools; reads the shared engine, or --engine local):
    statera query list | run <id> --param uf=SP | test
    statera metric balance --by region --period 2026-06
    statera recipes test
Copilot (optional: extra `copilot`; a model that runs on this computer, through Ollama):
    statera copilot "how many records did SP have in 2026?"   # finds the command or writes the SQL; asks before running
    statera copilot                      # a conversation
Demo:
    statera synthetic [--product foo] [--fault F]   # the products' synthetic sources
    statera demo                         # from scratch: synthetic data, editions, documentation
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _project():
    from statera.project import current

    return current()


def _default_product() -> str:
    """There is a default only when the repository has a single dissemination product; otherwise, --product."""
    public_products = [u.name for u in _project().products() if not u.internal]
    return public_products[0] if len(public_products) == 1 else ""


# --- production -----------------------------------------------------------------------------------


def _edition_result(ed) -> int:
    if ed.status == "blocked":
        print(f"\n[statera] EDITION BLOCKED ({ed.pending})", file=sys.stderr)
        for f in ed.failures[:15]:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


def _cmd_run(args) -> int:
    from statera import execution
    from statera.edition import EditionFailure

    try:
        ed = execution.run(
            args.product, args.period, args.cut_date, args.force, args.approve,
            with_dependencies=args.with_dependencies,
        )  # fmt: skip
    except EditionFailure as error:
        print(f"\n[statera] EDITION NOT RUN\n{error}", file=sys.stderr)
        return 1
    return _edition_result(ed)


def _cmd_resume(args) -> int:
    from statera import execution
    from statera.edition import EditionFailure

    key = args.period or args.cut_date
    decision = "rejected" if args.reject else "approved"
    try:
        ed = execution.resume(args.product, key, decision, args.approve, acknowledgement=args.acknowledge_alerts)
    except EditionFailure as error:  # nothing to resume (blocked, done), or alerts nobody acknowledged
        print(f"\n[statera] EDITION NOT RESUMED\n{error}", file=sys.stderr)
        return 1
    return _edition_result(ed)


def _cmd_status(args) -> int:
    from statera import execution

    if not (args.period or args.cut_date):
        return _status_overview(execution.overview(args.product, latest_only=not args.product))
    ed = execution.state(args.product, args.period or args.cut_date)
    print(
        f"{ed.product} {ed.period} (cut-off date {ed.cut_date}): {ed.status}"
        + (f", pending: {ed.pending}" if ed.pending else "")
    )
    for h in ed.history:
        print(f"  {h['task']:<22} {h['status']}{' (simulated)' if h.get('simulated') else ''}")
    if ed.alerts:
        print(f"\nEvaluation: {len(ed.alerts)} indicator(s) in alert")
        for a in ed.alerts:
            blocking = " — BLOCKING" if a.get("blocking") else ""
            print(f"  ⚠ {a['id']} {a['name']}: {a['formatted_value']} (alert {a['alert_text']}){blocking}")
        if ack := ed.acknowledgement:
            simulated = " (simulated)" if ack.get("simulated") else ""
            print(f"  acknowledged by {ack['by']} at {ack['at']} in {ack['task']}{simulated}: {ack['note']}")
        else:
            print('  not acknowledged: approve with statera resume --acknowledge-alerts "<why the edition goes on>"')
    if ed.failures and ed.status == "blocked":
        print("\nFailures:")
        for f in ed.failures[:15]:
            print(f"  - {f}")
    return 0


def _status_overview(rows: list[dict]) -> int:
    """One line per edition: where it is in the BPMN process, who has to act, and the next edition the
    product's calendar expects."""
    table = [("product", "edition", "status", "step", "role", "alerts", "next in the calendar")]
    for r in rows:
        if r["edition"] is None:
            table.append((r["product"], "—", "no runs", "", "", "", _next(r)))
            continue
        alerts = ""
        if r["alerts"]:
            state = "blocking" if r["blocking"] else ("acknowledged" if r["acknowledged"] else "not acknowledged")
            alerts = f"{r['alerts']} ⚠ {state}"
        table.append((r["product"], r["edition"], r["status"], r["step"], r["role"], alerts, _next(r)))
    _print_table(table)
    waiting = [r for r in rows if r.get("status") in ("waiting", "blocked")]
    if waiting:
        print()
        print(f"{len(waiting)} edition(s) need someone: statera tasks says who, and the command that takes each on")
    due = [r for r in rows if r.get("next") and r["next"]["state"] != "scheduled"]
    if due:
        print("\nDue by the calendar (the cut-off date has passed and it was not started):")
    for r in due:
        key = "--cut-date" if r["internal"] else "--period"
        print(f"  statera run --product {r['product']} {key} {r['next']['edition']}")
    return 0


def _next(row: dict) -> str:
    """The next edition of the calendar, on the last line of each product: `2026-03, cut-off 2026-04-10,
    release 2026-04-15 (late)`; `—` when the calendar has nothing left."""
    if "next" not in row:  # not the last line of its product
        return ""
    following = row["next"]
    if following is None:
        return "—"
    text = following["edition"]
    if not row["internal"]:
        text += f", cut-off {following['cut_date']}"
        text += f", release {following['release']}" if following["release"] else ""
    return text + ("" if following["state"] == "scheduled" else f" ({following['state']})")


def _print_table(table: list[tuple]) -> None:
    """The lines in columns, the first one as the header."""
    widths = [max(len(str(line[i])) for line in table) for i in range(len(table[0]))]
    for i, line in enumerate(table):
        print("  ".join(str(v).ljust(w) for v, w in zip(line, widths, strict=True)).rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))


def _cmd_tasks(args) -> int:
    """The human tasks of the open editions, then what there is to do now, by role."""
    from statera import execution, governance

    roles = governance.roles()
    rows = [r for r in execution.tasks(args.product, every=args.all) if not args.role or r["role"] == args.role]
    if not rows:
        print("No human task to show: no edition is open" + ("." if args.all else " (--all lists the ones done)."))
        return 0
    table = [("product", "edition", "task", "role", "state", "when (UTC)")]
    for r in rows:
        state = r["state"] + (" (simulated)" if r["simulated"] else "")
        table.append((r["product"], r["edition"], f"{r['name']} ({r['task']})", r["role"], state, _when(r)))
    _print_table(table)
    to_do = [r for r in rows if r["command"]]
    if to_do:
        print("\nTo do now:")
    for role in dict.fromkeys(r["role"] for r in to_do):
        who = roles.get(role, {})
        print(f"  {role}" + (f" — {who['name']}, {who['git_team']}" if who else ""))
        for r in (r for r in to_do if r["role"] == role):
            print(f"    {r['command']}")
            print(f"        {r['name']}: {r['does']}")
    return 0


def _when(row: dict) -> str:
    """When the task was completed; for the one an edition stopped at, since when, and how long ago."""
    from datetime import UTC, datetime

    if not row["at"]:
        return ""
    at = datetime.fromisoformat(row["at"])
    text = at.strftime("%Y-%m-%d %H:%M")
    if not row["command"]:
        return text
    minutes = int((datetime.now(UTC) - at).total_seconds() // 60)
    ago = f"{minutes} min" if minutes < 120 else f"{minutes // 60} h" if minutes < 2880 else f"{minutes // 1440} d"
    return f"since {text}, {ago}"


def _cmd_cuts(args) -> int:
    from statera.collect import read_cut, released_cuts

    p = _project()
    for u in [p.product(args.product)] if args.product else [u for u in p.products() if u.internal]:
        print(f"{u.name} ({u.alignment})")
        for cut_date in released_cuts(u):
            m = read_cut(u, cut_date)
            items = m.get("sources") or m.get("outputs") or {}
            summary = ", ".join(
                f"{k}: {v['rows']}" + (f" (batch {v['batch']})" if "batch" in v else "") for k, v in items.items()
            )
            print(f"  {cut_date}  run {m['run'][:8]}  {summary}")
    return 0


def _cmd_synthetic(args) -> int:
    from statera.extend import synthetic

    p = _project()
    try:
        generated = synthetic.run(p, product=args.product or None, fault=args.fault, references=args.references)
    except synthetic.SyntheticError as error:
        print(f"statera synthetic: {error}", file=sys.stderr)
        return 2
    for g in generated:
        if g.product is None:
            print(
                f"Note: the [demo] synthetic hook ({g.source}) is deprecated since statera 0.6; write SyntheticSource "
                "classes in <product>/3-build/synthetic.py",
                file=sys.stderr,
            )
        counts = ", ".join(f"{table}={n}" for table, n in g.counts.items()) or "nothing"
        where = f"{g.product}/{g.source}" if g.product else g.source
        print(f"Synthetic source {where}: {counts}" + (f" (injected fault: {g.fault})" if g.fault else ""))
    return 0


# --- quality ---------------------------------------------------------------------------------------


def _cmd_validate(args) -> int:
    from statera import validation

    return validation.run(generate_manifest=not args.no_dbt, verbose=args.verbose, level=args.level)


def _cmd_check(args) -> int:
    """Everything CI checks, in one command: validation, then lint and tests when the repository has them."""
    import importlib.util
    import subprocess

    from statera import launcher

    root = launcher.repository_root() or _project().root
    steps: list[tuple[str, int]] = []

    print("== validate", flush=True)
    steps.append(("validate", _cmd_validate(argparse.Namespace(no_dbt=args.no_dbt, verbose=False, level=None))))

    if importlib.util.find_spec("ruff"):
        print("\n== lint (ruff)", flush=True)
        code = subprocess.call([sys.executable, "-m", "ruff", "check", "."], cwd=root)
        code |= subprocess.call([sys.executable, "-m", "ruff", "format", "--check", "."], cwd=root)
        steps.append(("lint", code))
    else:
        print("\n== lint: skipped (ruff is not in the environment; add it to the dev dependency group)")

    if importlib.util.find_spec("pytest"):
        print("\n== tests" + ("" if args.all else " (fast; --all runs every test)"), flush=True)
        command = [sys.executable, "-m", "pytest", "-q"] + ([] if args.all else ["-m", "not slow"])
        code = subprocess.call(command, cwd=root)
        steps.append(("tests", 0 if code == 5 else code))  # 5 = no tests collected
    else:
        print("\n== tests: skipped (pytest is not in the environment; add it to the dev dependency group)")

    print("\n" + "  ".join(f"{'✔' if code == 0 else '✘'} {name}" for name, code in steps))
    return 1 if any(code for _, code in steps) else 0


def _cmd_doctor(args) -> int:
    from statera import doctor

    return doctor.report(doctor.run())


def _cmd_conformance(args) -> int:
    from statera import conformance

    failed = False
    for r in conformance.evaluate(forced_level=args.level):
        ok = r.achieved >= r.target
        failed |= not ok
        level_name = conformance.NAMES.get(r.achieved, "none")
        print(f"{'✔' if ok else '✘'} {r.unit.name}: level {r.achieved} ({level_name}); target {r.target}")
        for level, msg in r.outstanding:
            print(f"    outstanding for level {level}: {msg}")
    return 1 if failed else 0


def _cmd_contracts(args) -> int:
    from statera.analyse import verification

    results = verification.check_all()
    for r in results:
        if args.all_ or not r.ok:
            mark = "ok     " if r.ok else ("FAIL   " if r.blocking else "warning")
            print(f"[{mark}] {r.contract:40} {r.verification:40} {r.detail}")
    failures = [r for r in results if r.blocking]
    print(f"{len(results)} checks, {len(failures)} blocking failures")
    return 1 if failures else 0


# --- generated files --------------------------------------------------------------------------------


def _cmd_rules(args) -> int:
    from statera.process import rules

    if args.action == "sync":
        print(f"Generated: {_project().rel(rules.sync())}")
        return 0
    if args.action == "test":
        results = rules.run_python_cases()
        for r in results:
            print(f"[{r.status:^13}] {r.rule} / {r.case}" + (f": {r.message}" if r.message else ""))
        failures = sum(r.status == "failed" for r in results)
        print(f"{len(results)} rule case(s) in Python models, {failures} failure(s)")
        return 1 if failures else 0
    ok = rules.is_synced()
    print("Unit tests in sync with the rules." if ok else "Unit tests OUT OF DATE. Run: statera rules sync")
    return 0 if ok else 1


def _cmd_dbt(args) -> int:
    from statera.process import dbt

    if args.action == "generate":
        for c in dbt.sync():
            print(f"Generated: {_project().rel(c)}")
        return 0
    pending_items = dbt.outdated()
    for c in pending_items:
        print(f"out of date: {c} (run `statera dbt generate`)")
    return 1 if pending_items else 0


def _cmd_bpmn(args) -> int:
    from statera.design import bpmn, processes

    if args.action == "import":
        if not args.file:
            print("Give the .bpmn: statera bpmn import <product>/2-design/process.bpmn", file=sys.stderr)
            return 2
        print(f"YAML updated: {processes.import_bpmn(Path(args.file).resolve())}")
        return 0
    if args.action == "check":
        pending_items, orphans = processes.outdated(), processes.bpmn_without_source()
        for p in pending_items:
            print(f"out of date: {p} (run `statera bpmn`)")
        for b in orphans:
            print(f"no YAML source: {b} (run `statera bpmn import {b}`)")
        return 1 if pending_items or orphans else 0
    for path in processes.sync():
        print(f"BPMN: {_project().rel(path)}")
    for path in bpmn.render_all():
        print(f"viewer: {_project().rel(path)}")
    return 0


def _cmd_codeowners(args) -> int:
    from statera import governance

    print(f"Generated: {_rel_outside(governance.write_codeowners())}")
    return 0


def _cmd_sync(args) -> int:
    from statera import release_calendar

    for path in release_calendar.sync():
        print(f"Calendar (internal product): {path}")
    _cmd_dbt(argparse.Namespace(action="generate"))
    _cmd_rules(argparse.Namespace(action="sync"))
    _cmd_bpmn(argparse.Namespace(action="generate", file=None))
    return _cmd_codeowners(args)


def _cmd_lineage(args) -> int:
    from statera import lineage
    from statera.process import dbt

    dbt.manifest(generate=True)
    graph = lineage.build()
    if args.impact:
        try:
            report = lineage.impact_report(graph, args.impact)
        except KeyError as error:
            print(error.args[0], file=sys.stderr)
            return 2
        products = [r for r in report if r["kind"] == "product" and not r["same_unit"]]
        print(f"Changing '{args.impact}' affects {len(products)} other product(s):")
        for r in report:
            owner, who = r["owner"], ""
            if owner:
                who = f" · owner {owner['name']}" + (f" ({owner['git_team']})" if owner["git_team"] else "")
            print(f"\n  {r['title']}{' (same unit)' if r['same_unit'] else ''}{who}")
            for layer, _kind, label in r["assets"]:
                print(f"      {layer:9} {label}")
        teams = sorted({r["owner"]["git_team"] for r in products if r["owner"] and r["owner"]["git_team"]})
        if teams:
            print(f"\nNotify: {' '.join(teams)}")
        return 0
    if args.product:
        print(lineage.to_mermaid(lineage.neighborhood(graph, args.product), focus=args.product))
        return 0
    print(lineage.to_mermaid(graph))
    return 0


def _cmd_evaluate(args) -> int:
    from statera.evaluate import evaluation

    history = evaluation.history(_project().product(args.product))
    if history.empty:
        print("No edition published and evaluated yet.")
        return 1
    print(history.to_string(index=False))
    return 0


def _cmd_docs(args) -> int:
    from statera import documentation

    if getattr(args, "interactive", False):
        print(f"interactive catalog (open in the browser): {documentation.generate_interactive()}")
        return 0
    for path in documentation.generate():
        print(f"generated: {_project().rel(path)}")
    return 0


# --- consumption -------------------------------------------------------------------------------------


def _pairs(values: list[str] | None) -> dict:
    output = {}
    for item in values or []:
        key, _, value = item.partition("=")
        output[key.strip()] = value.strip()
    return output


def _cmd_query(args) -> int:
    from statera.consume import queries

    if args.action == "list":
        for c in queries.load():
            print(f"{c.id:<60} {c.meta['status']:<11} {c.title}")
        return 0
    if args.action == "test":
        targets = [queries.get(args.id)] if args.id else queries.load()
        failures = [f for c in targets for f in queries.test(c)]
        for f in failures:
            print(f"FAIL {f}")
        print(f"{len(targets)} query(ies) tested, {len(failures)} failure(s)")
        return 1 if failures else 0
    if args.action == "show":
        print(queries.compiled_sql(args.id, _pairs(args.param)), end="")
        return 0
    df = queries.run(args.id, _pairs(args.param), Path(args.output) if args.output else None, engine=args.engine)
    print(df.to_string(index=False))
    print(f"\nprovenance: {df.attrs['provenance']}")
    return 0


def _cmd_metric(args) -> int:
    from statera.consume import metrics

    by = [d for d in (args.by or "").split(",") if d]
    period_range = (args.from_, args.until) if args.from_ and args.until else None
    df = metrics.compute(args.name, by, args.period, period_range, _pairs(args.filter_), engine=args.engine)
    print(df.to_string(index=False))
    proc = {k: v for k, v in df.attrs["provenance"].items() if k != "sql"}
    print(f"\nprovenance: {proc}")
    return 0


def _cmd_recipes(args) -> int:
    from statera.consume import recipes

    entries = recipes.load()
    if args.action == "list":
        for r in entries:
            print(f"{r.path.name:<40} {r.title}")
        return 0
    failures = 0
    for r in entries:
        ok, output = recipes.run(r, engine=args.engine)
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {r.path.name}")
        if not ok or args.show:
            print("    " + output.replace("\n", "\n    "))
    print(f"{len(entries)} recipe(s), {failures} failure(s)")
    return 1 if failures else 0


def _cmd_copilot(args) -> int:
    from statera.copilot import console

    return console.main(args)


# --- maintenance and demo ----------------------------------------------------------------------


def _cmd_clean(args) -> int:
    p = _project()
    targets = [a for folder in p.data_folders() if folder.exists() for a in folder.glob("*") if a.name != "README.md"]
    targets += [p.dbt_project / "target", p.dbt_project / "logs"]
    if getattr(args, "registry", False):
        targets.append(p.edition_registry)  # demo only: in production the register is never deleted
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            continue
        print(f"removed: {p.rel(target)}")
    return 0


def _cmd_new_product(args) -> int:
    from copier import run_copy

    from statera.project import PACKAGE, read_yaml

    p = _project()
    destination = p.root / "products" / args.name
    if destination.exists():
        print(f"{destination} already exists", file=sys.stderr)
        return 1
    # the new product's roles start out equal to the shared ones (governance/shared-owners.yaml)
    shared = p.governance / "shared-owners.yaml"
    roles = read_yaml(shared) if shared.exists() else {}
    data = {
        "product": args.name,
        "namespace": p.namespace,
        "title": args.title or args.name,
        "alignment": args.alignment,
        **{k: roles[k] for k in ("curator", "technical_owner", "data_protection_officer") if k in roles},
    }
    model = PACKAGE / "templates" / "product"
    run_copy(str(model), str(destination), data=data, defaults=True, unsafe=False, quiet=True)
    # Copier records the template's absolute path: swap it for a relative one (does not expose the creator's machine)
    answers = destination / ".copier-answers.yml"
    if answers.exists():
        relative = os.path.relpath(model, destination)
        text = answers.read_text(encoding="utf-8").replace(str(model), relative)
        answers.write_text(text, encoding="utf-8")
    pending = " Declare authorizedConsumers in product.odps.yaml." if args.alignment != "consumer" else ""
    print(f"Product created at {p.rel(destination)}.{pending}")
    print("Next steps: fill in 1-specify/ (contract, classification, calendar); then `statera sync`, `statera check`.")
    return 0


def _generate_all(content: Path) -> None:
    """The generated files of a new repository (the same as `statera sync`), so it starts out valid."""
    import contextlib
    import io

    previous = os.environ.get("STATERA_PROJECT")
    os.environ["STATERA_PROJECT"] = str(content)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _cmd_sync(argparse.Namespace())
    finally:
        if previous is None:
            os.environ.pop("STATERA_PROJECT", None)
        else:
            os.environ["STATERA_PROJECT"] = previous


def _rel_outside(path: Path) -> str:
    """Path relative to the current folder, even outside the content (CODEOWNERS lives at the git root)."""
    return os.path.relpath(path)


def _cmd_init(args) -> int:
    """Creates a data repository from scratch from the `templates/repository` template (Copier)."""
    import re
    import subprocess

    from copier import run_copy

    from statera import __version__
    from statera.project import PACKAGE

    destination = Path(args.folder).resolve()
    if destination.exists() and any(destination.iterdir()):
        print(f"{destination} already exists and is not empty", file=sys.stderr)
        return 1
    name = args.name or destination.name
    if not re.fullmatch(r"[a-z0-9-]+", name):
        print(f"--name must have only lowercase letters, digits and hyphens: {name!r}", file=sys.stderr)
        return 2
    data = {
        "name": name,
        "organization": args.organization or name,
        "namespace": args.namespace or f"org.example.{name}",
        "acronym": args.acronym or name,
        "statera_git": args.source,
        "statera_version": args.version or __version__,
    }
    model = PACKAGE / "templates" / "repository"
    run_copy(str(model), str(destination), data=data, defaults=True, unsafe=False, quiet=True)
    answers = destination / ".copier-answers.yml"
    if answers.exists():  # without the absolute path of the creator's machine
        text = answers.read_text(encoding="utf-8")
        answers.write_text(text.replace(str(model), f"statera:templates/repository@{__version__}"), "utf-8")
    _generate_all(destination / "content")
    inside_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=destination, capture_output=True, text=True
    ).returncode == 0  # fmt: skip
    if not inside_git and shutil.which("git") and not args.no_git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=destination, check=False)
        if os.name == "nt":  # product folders get deep; Windows' 260-character limit would bite
            subprocess.run(["git", "config", "core.longpaths", "true"], cwd=destination, check=False)
    print(f"Data repository created at {destination}.")
    print("Next steps:")
    relative = os.path.relpath(destination)
    print(f"  cd {relative if not relative.startswith('../..') else destination}")
    print("  statera doctor                     # environment, pinned Statera version, git settings")
    print("  statera check                      # the repository starts out valid")
    print("  statera new-product my-statistic   # the first statistic")
    if data["namespace"].startswith("org.example."):
        print(f"Note: provisional namespace ({data['namespace']}); adjust it in content/statera.toml.")
    return 0


def _cmd_demo(args) -> int:
    from statera import documentation, execution

    p = _project()
    _cmd_clean(argparse.Namespace(registry=True))
    _cmd_sync(args)
    if code := _cmd_synthetic(argparse.Namespace(product=None, fault=None, references=False)):
        return code
    for edition in p.demo.get("editions", []):
        print(f"\n[statera] ==== {edition['product']} {edition['period']} ====", flush=True)
        ed = execution.run(edition["product"], edition["period"], approve=True, with_dependencies=True)
        if ed.status != "done":
            return _edition_result(ed) or 1
    for path in documentation.generate():
        print(f"generated: {p.rel(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from statera import launcher

    if argv is None:  # the console command; programmatic calls (tests, other code) never hand off
        handed_off = launcher.handoff(sys.argv[1:])
        if handed_off is not None:
            return handed_off

    raw = sys.argv[1:] if argv is None else argv
    if _query_help_requested(raw):  # `statera query run|show|test <id> --help`: the query's own help
        from statera.consume import queries

        try:
            print(queries.describe(raw[2]), end="")
        except queries.QueryError as error:
            print(error, file=sys.stderr)
            return 2
        return 0

    args = parse(raw)
    problem = None if args.command in ("init", "doctor") else launcher.launcher_problem() or _version_problem()
    if problem:
        print(f"[statera] {problem}", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except _expected_errors() as error:
        print(f"[statera] {error}", file=sys.stderr)
        return 1
    except ModuleNotFoundError as error:
        if error.name in COPILOT_MODULES:
            print(f"[statera] {MISSING_COPILOT.format(module=error.name)}", file=sys.stderr)
            return 1
        if error.name not in PRODUCTION_MODULES:
            raise
        print(f"[statera] {MISSING_PRODUCTION.format(module=error.name)}", file=sys.stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    """Every command and its options. Also what the copilot reads to know the commands and to check the
    ones it proposes (statera.copilot)."""
    from statera import __version__

    p = argparse.ArgumentParser(
        prog="statera", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"statera {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    product = _safe_default_product()

    s = sub.add_parser("check", help="what CI runs: validation, lint and tests (fast ones unless --all)")
    s.add_argument("--all", dest="all", action="store_true", help="also runs the slow tests (full process)")
    s.add_argument("--no-dbt", dest="no_dbt", action="store_true", help="uses the existing manifest")
    s.set_defaults(func=_cmd_check)

    sub.add_parser("doctor", help="checks the computer and the repository; says how to fix each problem").set_defaults(
        func=_cmd_doctor
    )

    s = sub.add_parser("run", help="runs an edition (or, for an internal product, a cut) through the product's process")
    s.add_argument("--product", dest="product", default=product)
    s.add_argument("--period", dest="period", help="required for a dissemination product")
    s.add_argument(
        "--cut-date", dest="cut_date", help="default: the one in calendar.yaml; required for an internal product"
    )
    s.add_argument(
        "--with-dependencies",
        dest="with_dependencies",
        action="store_true",
        help="first releases the missing cuts of the input internal products",
    )
    s.add_argument(
        "--force", dest="force", action="store_true", help="republishes (correction) an already published edition"
    )
    s.add_argument("--approve", dest="approve", action="store_true", help="simulates the human tasks (demo)")
    s.set_defaults(func=_cmd_run)

    s = sub.add_parser(
        "resume",
        help="completes the pending human task (approves it, or rejects with --reject; or re-evaluates the wait) "
        "and resumes",
    )
    s.add_argument("--product", dest="product", default=product)
    s.add_argument("--period", dest="period")
    s.add_argument("--cut-date", dest="cut_date", help="for an internal product, the run key")
    s.add_argument("--reject", dest="reject", action="store_true", help="records the decision as rejected")
    s.add_argument("--approve", dest="approve", action="store_true", help="simulates the following human tasks")
    s.add_argument(
        "--acknowledge-alerts", dest="acknowledge_alerts", metavar="NOTE",
        help="acknowledges the evaluation's alerts (required to approve when there are any): why the edition goes on",
    )  # fmt: skip
    s.set_defaults(func=_cmd_resume)

    s = sub.add_parser(
        "status",
        help="where the editions are: all products (latest edition, and the next one the calendar expects), "
        "one product (every edition), or one edition",
    )
    s.add_argument("--product", dest="product", help="without it: every product, latest edition or cut")
    s.add_argument("--period", dest="period", help="with --product: one edition in detail (tasks, alerts)")
    s.add_argument("--cut-date", dest="cut_date", help="with --product: one cut of an internal product")
    s.set_defaults(func=_cmd_status)

    s = sub.add_parser(
        "tasks",
        help="the human tasks of every open edition: who (which role) has to do what, since when, and the "
        "command that takes each one on",
    )
    s.add_argument("--product", dest="product", help="only the editions of this product (without it: every product)")
    s.add_argument(
        "--role", dest="role", choices=_roles(),
        help="only the tasks of this role, an id of governance/roles.yaml (without it: every role)",
    )  # fmt: skip
    s.add_argument("--all", dest="all", action="store_true", help="also the editions done: what was completed, when")
    s.set_defaults(func=_cmd_tasks)

    s = sub.add_parser("cuts", help="cuts released by the internal products")
    s.add_argument("--product", dest="product")
    s.set_defaults(func=_cmd_cuts)

    s = sub.add_parser(
        "synthetic", help="generates the synthetic sources (SyntheticSource classes), in dependency order"
    )
    s.add_argument("--product", dest="product", help="only this product's sources (default: every product)")
    s.add_argument(
        "--fault", dest="fault", help="injects a fault declared by a source (`faults`), to exercise the checks"
    )
    s.add_argument(
        "--references", dest="references", action="store_true", help="also regenerates the synthetic reference tables"
    )
    s.set_defaults(func=_cmd_synthetic)

    s = sub.add_parser("validate", help="static validation of the repository")
    s.add_argument("--level", dest="level", type=int, choices=[1, 2, 3, 4], help="checks only what the level requires")
    s.add_argument(
        "--no-dbt", dest="no_dbt", action="store_true", help="uses the existing manifest (does not run dbt parse)"
    )
    s.add_argument("--verbose", dest="verbose", action="store_true", help="also lists the checks that passed")
    s.set_defaults(func=_cmd_validate)

    s = sub.add_parser("conformance", help="conformance level of each product")
    s.add_argument("--level", dest="level", type=int, choices=[1, 2, 3, 4])
    s.set_defaults(func=_cmd_conformance)

    s = sub.add_parser("contracts", help="checks the ODCS contracts against the engine")
    s.add_argument("--all", dest="all_", action="store_true")
    s.set_defaults(func=_cmd_contracts)

    s = sub.add_parser("rules", help="unit tests generated from the rules (SQL) and rule cases in Python models")
    s.add_argument("action", choices=["sync", "check", "test"])
    s.set_defaults(func=_cmd_rules)

    s = sub.add_parser("dbt", help="dbt_project.yml and reference table properties, generated from the units")
    s.add_argument("action", choices=["generate", "check"])
    s.set_defaults(func=_cmd_dbt)

    s = sub.add_parser("bpmn", help="generates .bpmn from the process YAML and renders SVG/HTML")
    s.add_argument("action", nargs="?", default="generate", choices=["generate", "import", "check"])
    s.add_argument("file", nargs="?")
    s.set_defaults(func=_cmd_bpmn)

    sub.add_parser("codeowners", help="generates .github/CODEOWNERS").set_defaults(func=_cmd_codeowners)
    sub.add_parser("sync", help="regenerates all generated files").set_defaults(func=_cmd_sync)

    s = sub.add_parser("lineage", help="lineage in Mermaid (all products, or one) or impact analysis by product")
    s.add_argument(
        "--product", dest="product", help="one product (or reference table) with its upstream and downstream"
    )
    s.add_argument("--impact", dest="impact", help="asset, product or reference table: what is downstream, by product")
    s.set_defaults(func=_cmd_lineage)

    s = sub.add_parser("evaluate", help="quality indicators of the published editions (phase 8)")
    s.add_argument("--product", dest="product", default=product)
    s.set_defaults(func=_cmd_evaluate)

    s = sub.add_parser("docs", help="regenerates docs/ and the editions register page")
    s.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        help="generates the browsable, searchable catalog (dbt docs as a single page) in target/static_index.html",
    )
    s.set_defaults(func=_cmd_docs)

    s = sub.add_parser("query", help="cataloged recurring queries")
    s.add_argument(
        "action", choices=["list", "show", "run", "test"],
        help="show: the SQL that run executes; `statera query show <id> --help` explains the query's parameters",
    )  # fmt: skip
    s.add_argument("id", nargs="?")
    s.add_argument("--param", action="append", help="name=value (repeatable)")
    s.add_argument("--output", dest="output", help="folder for result.csv and the provenance datapackage.json")
    s.add_argument("--engine", dest="engine", help=ENGINE_HELP)
    s.set_defaults(func=_cmd_query)

    s = sub.add_parser("metric", help="computes an official metric")
    s.add_argument("name")
    s.add_argument("--by", dest="by", help="comma-separated dimensions")
    s.add_argument("--period", dest="period")
    s.add_argument("--from", dest="from_")
    s.add_argument("--to", dest="until")
    s.add_argument("--filter", dest="filter_", action="append", help="dimension=value (repeatable)")
    s.add_argument("--engine", dest="engine", help=ENGINE_HELP)
    s.set_defaults(func=_cmd_metric)

    s = sub.add_parser("recipes", help="lists or runs the recipes")
    s.add_argument("action", choices=["list", "test"])
    s.add_argument("--show", dest="show", action="store_true", help="shows the output of each recipe")
    s.add_argument("--engine", dest="engine", help=ENGINE_HELP)
    s.set_defaults(func=_cmd_recipes)

    s = sub.add_parser("init", help="creates a data repository from scratch (content/, publications/, docs/)")
    s.add_argument("folder", help="folder to create (empty or nonexistent)")
    s.add_argument("--name", dest="name", help="repository name (lowercase and hyphens); default: the folder name")
    s.add_argument("--organization", dest="organization", help="organization or program, in full")
    s.add_argument("--namespace", dest="namespace", help="namespace, e.g. org.example")
    s.add_argument("--acronym", dest="acronym", help="short acronym (default: the name)")
    s.add_argument(
        "--statera-source", dest="source", default="ssh://git@github.com/Jaimenms/statera.git",
        help="git repository from which pyproject.toml installs Statera",
    )  # fmt: skip
    s.add_argument("--statera-version", dest="version", help="version (tag v<version>); default: the installed one")
    s.add_argument("--no-git", dest="no_git", action="store_true", help="does not run git init")
    s.set_defaults(func=_cmd_init)

    s = sub.add_parser("new-product", help="creates a product from the template (Copier)")
    s.add_argument("name")
    s.add_argument("--title", dest="title")
    s.add_argument(
        "--alignment", dest="alignment", choices=["consumer", "aggregate", "source"], default="consumer",
        help="consumer = dissemination; aggregate or source = internal product (specification 09)",
    )  # fmt: skip
    s.set_defaults(func=_cmd_new_product)

    s = sub.add_parser("clean", help="deletes the units' data/ folders and the dbt artifacts")
    s.add_argument(
        "--registry", dest="registry", action="store_true", help="also deletes the editions register (demo only)"
    )
    s.set_defaults(func=_cmd_clean)

    sub.add_parser("demo", help="demo from scratch (deletes data and the editions register)").set_defaults(
        func=_cmd_demo
    )

    s = sub.add_parser(
        "copilot",
        help="ask in plain language: a local model finds the command (or writes the SQL) and runs it if you agree",
    )
    s.add_argument("question", nargs="*", help="the question; without it, a conversation starts")
    s.add_argument(
        "--yes", dest="yes", action="store_true", help="runs what only reads (queries, status…) without asking"
    )
    s.add_argument("--model", dest="model", help="local Ollama model (default: [copilot] model, else qwen2.5-coder:7b)")
    s.add_argument("--output", dest="output", help="folder for result.csv and query.sql of an ad hoc SQL answer")
    s.add_argument("--engine", dest="engine", help=ENGINE_HELP)
    s.add_argument("--verbose", dest="verbose", action="store_true", help="shows the model's reasoning at each step")
    s.set_defaults(func=_cmd_copilot)

    return p


def parse(argv: list[str]) -> argparse.Namespace:
    """The command's arguments, checked. A problem ends as argparse ends it: the message on stderr and
    SystemExit(2)."""
    p = parser()
    args = p.parse_args(argv)
    if getattr(args, "product", "x") == "" and args.command in ("run", "resume", "evaluate"):
        names = ", ".join(u.name for u in _products())
        p.error("give --product" + (f" (products in this repository: {names})" if names else ""))
    if args.command == "resume" and not (args.period or args.cut_date):
        p.error("give --period (or --cut-date, for an internal product)")
    if args.command == "query" and args.action in ("run", "show") and not args.id:
        p.error("give the query id")
    if args.command == "status" and (args.period or args.cut_date) and not args.product:
        p.error("give --product with --period or --cut-date")
    return args


def _roles() -> list[str] | None:
    """The roles of the repository, as the choices of an option (None, any value, outside a repository)."""
    try:
        from statera import governance

        return list(governance.roles(_project())) or None
    except Exception:  # outside a content repository, or roles.yaml missing
        return None


def _products() -> list:
    try:
        return _project().products()
    except Exception:  # outside a content repository
        return []


ENGINE_HELP = "where to read the data: `local`, a DuckDB file or URL, or an engine URL (default: statera.toml)"
PRODUCTION_MODULES = {"copier", "dbt"}
COPILOT_MODULES = {"httpx"}
MISSING_COPILOT = (
    "`{module}` is not installed: `statera copilot` is optional (extra `copilot`). In a data repository, declare "
    'copilot = ["statera[copilot]"] in [dependency-groups] of pyproject.toml and run `statera copilot` again '
    "(with uv alone: `uv run --group copilot statera copilot`). "
    'Outside one: uv tool install --force "statera[copilot] @ git+ssh://git@github.com/Jaimenms/statera.git". '
    "The model runs on this computer, through Ollama (https://ollama.com); nothing is sent anywhere else."
)
MISSING_PRODUCTION = (
    "`{module}` is not installed: this command needs the production tools (extra `produce`). In a data "
    'repository, declare produce = ["statera[produce]"] in [dependency-groups] of pyproject.toml and run the '
    "command through `statera` (or `uv sync --all-groups`). For `statera init`, install the global command with "
    'them: uv tool install --force "statera[produce] @ git+ssh://git@github.com/Jaimenms/statera.git"'
)


def _expected_errors() -> tuple[type[Exception], ...]:
    """Errors whose message says what to do: shown without a traceback (a reader is not a developer)."""
    from statera.consume.catalog import AccessDenied, CutNotAvailable
    from statera.consume.connection import EngineNotAvailable, PublicationNotAvailable
    from statera.consume.metrics import MetricError
    from statera.consume.queries import QueryError
    from statera.process.dbt import ProductionToolsMissing

    return (
        EngineNotAvailable,
        PublicationNotAvailable,
        QueryError,
        MetricError,
        AccessDenied,
        CutNotAvailable,
        ProductionToolsMissing,
    )


def _query_help_requested(argv: list[str]) -> bool:
    return (
        len(argv) >= 4
        and argv[0] == "query"
        and argv[1] in ("run", "show", "test")
        and not argv[2].startswith("-")
        and any(a in ("-h", "--help") for a in argv[3:])
    )


def _version_problem() -> str | None:
    """The repository's `[project] required_version` against this version (None outside a repository)."""
    from statera import launcher
    from statera.project import ProjectError

    try:
        required = _project().required_version
    except ProjectError:
        return None
    return launcher.version_problem(required)


def _safe_default_product() -> str:
    try:
        return _default_product()
    except Exception:  # outside a content repository: only --help works
        return ""


if __name__ == "__main__":
    sys.exit(main())
