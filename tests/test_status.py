"""`statera status`: where every product's editions are in their BPMN process, and who has to act."""

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
    assert "Approve (T_H)" in out and "no runs" in out and "1 edition(s) need someone" in out


def test_one_product_lists_every_edition(minimal_project, capsys):
    execution.run("sample", "2026-01", approve=True)
    execution.run("sample", "2026-02", cut_date="2026-03-20")
    assert [r["edition"] for r in execution.overview("sample", latest_only=False)] == ["2026-01", "2026-02"]
    cli.main(["status", "--product", "sample"])
    out = capsys.readouterr().out
    assert "2026-01" in out and "done" in out and "2026-02" in out and "waiting" in out
