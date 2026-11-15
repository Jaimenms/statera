"""bar's own edition checks (statera.extend.Check), named by the controls in controls.yaml.

Each check has its cases in check-cases/<Check>.yaml, run by `statera validate` and `statera check`.
"""

from __future__ import annotations

from statera.extend import Check, CheckContext, CheckResult


class NoRecordsLost(Check):
    """Records of the Foo System only arrive late, they never leave: for every key already released in the
    previous edition, the measure must not decrease. A decrease means records lost between two foo cuts.

    Parameters (controls.yaml): `port`, `key` (columns), `measure` (default `records`)."""

    severity = "error"

    def evaluate(self, ctx: CheckContext) -> CheckResult:
        port, key = ctx.params["port"], list(ctx.params["key"])
        measure = ctx.params.get("measure", "records")
        previous = ctx.previous(port)
        if previous is None or previous.empty:
            return CheckResult.ok("no previous edition to compare with")
        current = ctx.current(port)
        both = previous[[*key, measure]].merge(
            current[[*key, measure]], on=key, how="left", suffixes=("_previous", "_current")
        )
        both[f"{measure}_current"] = both[f"{measure}_current"].fillna(0)
        lost = both[both[f"{measure}_current"] < both[f"{measure}_previous"]]
        if lost.empty:
            return CheckResult.ok(f"{len(both)} key(s) released before: none lost {measure}")
        examples = "; ".join(
            f"{'/'.join(str(row[k]) for k in key)}: {row[f'{measure}_previous']:g} -> {row[f'{measure}_current']:g}"
            for _, row in lost.head(3).iterrows()
        )
        return CheckResult.fail(f"{len(lost)} key(s) lost {measure} since the previous edition: {examples}", rows=lost)
