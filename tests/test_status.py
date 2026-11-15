"""`statera status`: where every product's editions are in their BPMN process, and who has to act.
`statera tasks`: the human tasks of the open editions, and the command that takes each one on."""

from datetime import date

import pytest

from statera import cli, execution


def test_overview_shows_the_step_and_the_role_of_each_product(minimal_project, capsys):
    execution.run("sample", "2026-01")  # stops at the human task T_H (Approve)
    rows = {r["product"]: r for r in execution.overview()}
    assert (rows["sample"]["status"], rows["sample"]["step"], rows["sample"]["role"]) == (
        "waiting", "Approve (T_H)", "team-x",
    )  # fmt: skip
    assert rows["consumer"]["status"] == "no runs"
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Approve (T_H)" in out and "no runs" in out and "1 edition(s) need someone: statera tasks" in out


def test_one_product_lists_every_edition(minimal_project, capsys):
    execution.run("sample", "2026-01", approve=True)
    execution.run("sample", "2026-02", cut_date="2026-03-20")
    assert [r["edition"] for r in execution.overview("sample", latest_only=False)] == ["2026-01", "2026-02"]
    cli.main(["status", "--product", "sample"])
    out = capsys.readouterr().out
    assert "2026-01" in out and "done" in out and "2026-02" in out and "waiting" in out


def test_the_next_edition_comes_from_the_calendar_not_from_the_runs(minimal_project, capsys):
    from statera import release_calendar

    release_calendar.sync(minimal_project)  # the internal product's calendar: the cuts its consumers request
    consumer, base = minimal_project.product("consumer"), minimal_project.product("base-data")
    before, on_the_day, after = date(2026, 2, 1), date(2026, 2, 20), date(2026, 3, 1)
    expected = {"edition": "2026-01", "cut_date": "2026-02-20", "release": "2026-02-27"}
    assert execution.next_expected(consumer, before) == {**expected, "state": "scheduled"}  # with no run at all
    assert execution.next_expected(consumer, on_the_day) == {**expected, "state": "due"}
    assert execution.next_expected(consumer, after) == {**expected, "state": "late"}
    assert execution.next_expected(base, before) == {
        "edition": "2026-02-20", "cut_date": "2026-02-20", "release": None, "state": "scheduled",
    }  # fmt: skip

    rows = {r["product"]: r for r in execution.overview(today=after)}
    assert rows["sample"]["status"] == "no runs" and rows["sample"]["next"]["edition"] == "2026-01"
    execution.run("base-data", cut_date="2026-02-20")
    execution.run("consumer", "2026-01")
    assert execution.next_expected(base, after) is None  # released: the calendar has nothing left
    assert execution.next_expected(consumer, after)["edition"] == "2026-02"  # 2026-01 was started

    cli.main(["status"])
    out = capsys.readouterr().out  # today is after every date of the fixture's calendars
    line = next(line for line in out.splitlines() if line.startswith("consumer"))
    assert line.endswith("2026-02, cut-off 2026-02-20, release 2026-03-27 (late)")
    assert next(line for line in out.splitlines() if line.startswith("base-data")).endswith("—")
    due = out.split("Due by the calendar")[1]
    assert "  statera run --product consumer --period 2026-02\n" in due
    assert "  statera run --product sample --period 2026-01\n" in due and "base-data" not in due

    cli.main(["status", "--product", "consumer"])  # every edition: the next one on the product's last line
    assert capsys.readouterr().out.count(", cut-off ") == 1


def test_an_edition_in_the_register_is_not_expected_again(minimal_project, monkeypatch):
    from statera.disseminate import registry

    sample = minimal_project.product("sample")
    (sample.phase(7) / "series.yaml").parent.mkdir(parents=True, exist_ok=True)
    (sample.phase(7) / "series.yaml").write_text("id: sample\n", encoding="utf-8")
    monkeypatch.setattr(registry, "is_published", lambda unit, period: period == "2026-01")
    assert execution.next_expected(sample, date(2026, 3, 1)) is None  # published, though its runs are gone


def _tasks(product=None, **options) -> list[tuple]:
    return [(r["product"], r["edition"], r["task"], r["state"]) for r in execution.tasks(product, **options)]


def test_tasks_lists_the_human_tasks_of_the_open_editions_and_what_to_run(minimal_project, capsys):
    execution.run("sample", "2026-01")  # stops at the human task T_H; T_P comes after; T_X is the exception
    assert _tasks() == [("sample", "2026-01", "T_H", "waiting"), ("sample", "2026-01", "T_P", "not reached")]
    waiting = execution.tasks()[0]
    assert (waiting["role"], waiting["command"]) == ("team-x", "statera resume --product sample --period 2026-01")
    assert waiting["at"] and execution.tasks()[1]["command"] is None  # since the task before it ended

    assert cli.main(["tasks"]) == 0
    out = capsys.readouterr().out
    assert "Approve (T_H)" in out and "since 20" in out and "0 min" in out
    to_do = out.split("To do now:\n")[1]
    assert to_do.startswith("  team-x — Team X, @org/team-x\n    statera resume --product sample --period 2026-01\n")
    assert "completes it as approved; --reject, as rejected" in to_do

    execution.resume("sample", "2026-01")  # approved: the edition now waits at T_P
    assert [row[2:] for row in _tasks()] == [("T_H", "approved"), ("T_P", "waiting")]
    execution.resume("sample", "2026-01")  # done: nothing left for anyone, unless asked for
    assert _tasks() == [] and [row[3] for row in _tasks(every=True)] == ["approved", "approved"]
    cli.main(["tasks"])
    assert "no edition is open (--all lists the ones done)" in capsys.readouterr().out
    cli.main(["tasks", "--all"])
    out = capsys.readouterr().out
    assert out.count("approved") == 2 and "To do now" not in out and "since" not in out


def test_tasks_says_what_to_do_about_a_blocked_edition_and_about_a_wait(minimal_project, monkeypatch, capsys):
    execution.run("consumer", "2026-01")  # awaits the cut of base-data: not a human task, and someone acts
    wait = execution.tasks("consumer")[0]
    assert (wait["task"], wait["state"]) == ("Ev_Cut", "waiting") and wait["at"]  # since the edition started
    assert (wait["command"], wait["does"]) == (
        "statera resume --product consumer --period 2026-01", "goes on, once what it awaits is there",
    )  # fmt: skip

    monkeypatch.setenv("STEP_DECISION", "failed")
    execution.run("sample", "2026-01")  # the exception task is listed, as where the edition stopped
    assert _tasks("sample") == [
        ("sample", "2026-01", "T_X", "blocked"), ("sample", "2026-01", "T_H", "not reached"),
        ("sample", "2026-01", "T_P", "not reached"),
    ]  # fmt: skip
    blocked = execution.tasks("sample")[0]
    assert blocked["command"] == "statera run --product sample --period 2026-01"
    assert "statera status --product sample --period 2026-01" in blocked["does"]

    assert cli.main(["tasks", "--role", "team-x"]) == 0
    assert "statera run --product sample --period 2026-01" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # a role is one of governance/roles.yaml
        cli.main(["tasks", "--role", "nobody"])
    assert "argument --role: invalid choice: 'nobody' (choose from team-x)" in capsys.readouterr().err
