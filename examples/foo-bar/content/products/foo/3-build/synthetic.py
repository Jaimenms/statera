"""SYNTHETIC data generator for the Foo System (the [demo] synthetic hook in statera.toml).

Writes the "source" that the extraction reads: a Parquet file with one record per request, with the event date
and the reception date (some arrive late, so that the same reference month changes between cuts).
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta

import pandas as pd

SEED = 20261114
WEIGHTED_UFS = {"SP": 8, "MG": 4, "RJ": 3, "BA": 2, "RS": 2, "PE": 1, "DF": 1}


def generate(project, failure: str | None = None, references: bool = False) -> dict[str, int]:
    rng = random.Random(SEED)
    ufs = {u["uf_abbr"]: int(u["uf_id"]) for u in csv.DictReader((project.root / "references/uf/ref_uf.csv").open())}
    uf_codes, weights = list(WEIGHTED_UFS), list(WEIGHTED_UFS.values())
    lines = []
    for i in range(1, 1201):
        fact = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 59))  # January and February
        delay = rng.choice([1, 2, 3, 5, 8, 15, 40])  # some arrive after the cut
        acronym = rng.choices(uf_codes, weights)[0]
        lines.append(
            {
                "record_id": f"R{i:05d}",
                "person_id": f"P{rng.randrange(1, 900):04d}",
                "uf_id": ufs[acronym],
                "event_date": fact,
                "received_at": datetime.combine(fact + timedelta(days=delay), datetime.min.time()),
                "value": round(rng.uniform(50, 500), 2),
            }
        )
    destination = project.product("foo").data(4) / "source"
    destination.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(lines).to_parquet(destination / "foo_records.parquet", index=False)
    return {"foo_records": len(lines)}
