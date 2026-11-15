"""The `statera` command line. Run it from any folder of a data repository: it uses the Statera version
the repository pins (a global `statera` hands off to it; see launcher.py).

Getting started:
    statera init my-repo --name stats-example --namespace org.example --organization "..."
    statera doctor                       # checks the computer and the repository, says how to fix
    statera new-product my-statistic
Day to day:
    statera check                        # what CI runs: validation, lint and fast tests (--all: every test)
Production (the product's to-be process):
    statera run --product foo --period 2026-06                    # stops at the 1st human task
    statera run --product foo --period 2026-06 --with-dependencies
    statera run --product bar-events --cut-date 2026-07-20         # internal product: one cut
    statera resume --product foo --period 2026-06                 # completes the task and continues
    statera status --product foo --period 2026-06
    statera cuts                                                  # cuts released by the internal products
Quality and conformance:
    statera validate [--level 2]         statera conformance          statera contracts
Generated files:
    statera sync                         # dbt_project.yml, rule unit tests, BPMN, CODEOWNERS
Consumption (needs no production tools; reads the shared engine, or --engine local):
    statera query list | run <id> --param uf=SP | test
    statera metric balance --by region --period 2026-06
    statera recipes test
Demo:
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

    key = args.period or args.cut_date
    ed = execution.resume(args.product, key, "rejected" if args.reject else "approved", args.approve)
    return _edition_result(ed)


def _cmd_status(args) -> int:
    from statera import execution

    ed = execution.state(args.product, args.period or args.cut_date)
    print(
        f"{ed.product} {ed.period} (cut-off date {ed.cut_date}): {ed.status}"
        + (f", pending: {ed.pending}" if ed.pending else "")
    )
    for h in ed.history:
        print(f"  {h['task']:<22} {h['status']}{' (simulated)' if h.get('simulated') else ''}")
    return 0


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
    p = _project()
    if not p.demo.get("synthetic"):
        print("statera.toml does not declare [demo] synthetic", file=sys.stderr)
        return 2
    counts = p.hook(p.demo["synthetic"])(p, failure=args.failure, references=args.references)
    print(f"Synthetic sources generated: {counts}" + (f" (injected fault: {args.failure})" if args.failure else ""))
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
            print("Give the .bpmn: statera bpmn import <unit>/2-design/processes/<file>.bpmn", file=sys.stderr)
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
    _cmd_synthetic(argparse.Namespace(failure=None, references=False))
    for edition in p.demo.get("editions", []):
        print(f"\n[statera] ==== {edition['product']} {edition['period']} ====", flush=True)
        ed = execution.run(edition["product"], edition["period"], approve=True, with_dependencies=True)
        if ed.status != "done":
            return _edition_result(ed) or 1
    for path in documentation.generate():
        print(f"generated: {p.rel(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from statera import __version__, launcher

    if argv is None:  # the console command; programmatic calls (tests, other code) never hand off
        handed_off = launcher.handoff(sys.argv[1:])
        if handed_off is not None:
            return handed_off

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

    s = sub.add_parser("run", help="runs an edition (or, for an internal product, a cut) through the to-be process")
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

    s = sub.add_parser("resume", help="completes the pending human task (or re-evaluates the wait) and resumes")
    s.add_argument("--product", dest="product", default=product)
    s.add_argument("--period", dest="period")
    s.add_argument("--cut-date", dest="cut_date", help="for an internal product, the run key")
    s.add_argument("--reject", dest="reject", action="store_true", help="records the decision as rejected")
    s.add_argument("--approve", dest="approve", action="store_true", help="simulates the following human tasks")
    s.set_defaults(func=_cmd_resume)

    s = sub.add_parser("status", help="status of an edition and completed tasks")
    s.add_argument("--product", dest="product", default=product)
    s.add_argument("--period", dest="period")
    s.add_argument("--cut-date", dest="cut_date")
    s.set_defaults(func=_cmd_status)

    s = sub.add_parser("cuts", help="cuts released by the internal products")
    s.add_argument("--product", dest="product")
    s.set_defaults(func=_cmd_cuts)

    s = sub.add_parser("synthetic", help="generates the synthetic sources ([demo] synthetic hook)")
    s.add_argument(
        "--fault", dest="failure", choices=["distribution"], help="injects an error to demonstrate the controls"
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
    s.add_argument("action", choices=["list", "run", "test"])
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

    args = p.parse_args(argv)
    problem = None if args.command in ("init", "doctor") else launcher.launcher_problem() or _version_problem()
    if problem:
        print(f"[statera] {problem}", file=sys.stderr)
        return 2
    if getattr(args, "product", "x") == "" and args.command in ("run", "resume", "status", "evaluate"):
        p.error("give --product")
    if args.command in ("resume", "status") and not (args.period or args.cut_date):
        p.error("give --period (or --cut-date, for an internal product)")
    if args.command == "query" and args.action == "run" and not args.id:
        p.error("give the query id")
    if args.command in ("run", "resume", "status", "evaluate") and not args.product:
        names = ", ".join(u.name for u in _project().products())
        print(f"give --product (products in this repository: {names})", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except _expected_errors() as error:
        print(f"[statera] {error}", file=sys.stderr)
        return 1
    except ModuleNotFoundError as error:
        if error.name not in PRODUCTION_MODULES:
            raise
        print(f"[statera] {MISSING_PRODUCTION.format(module=error.name)}", file=sys.stderr)
        return 1


ENGINE_HELP = "where to read the data: `local`, a DuckDB file or URL, or an engine URL (default: statera.toml)"
PRODUCTION_MODULES = {"copier", "dbt"}
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
