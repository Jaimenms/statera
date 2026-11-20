"""What the model is shown: one card per command, table, cataloged query, metric and product.

The cards are built from the repository when the copilot starts (the command line's own parser, the
contracts, `queries/`, the metrics, the product descriptors): there is no index to keep up to date, and what
the model reads is what the commands read. Each card has an id (`table:gold.sales_monthly`), a one-line
summary (what the model chooses from) and the card in full (what it gets once it chose).

A repository small enough is listed whole. For a larger one, `search` ranks the cards by the words of the
request (BM25 over the summary, the columns, the parameters) and the best ones are the candidates. It is
deliberately plain, with no embeddings: when the candidates do not fit, the model asks for another search
with words of its own (a synonym, the catalog's language).
"""

from __future__ import annotations

import argparse
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

DATA = ("query", "metric", "table")  # the kinds of card that say what the data is
LAYER_ORDER = {"gold": 0, "ref": 1, "silver": 2, "bronze": 3}
LISTED = 40  # queries, metrics and tables that a repository may have and still be listed whole to the model


@dataclass(frozen=True)
class Card:
    id: str  # "<kind>:<name>"
    summary: str  # one line
    detail: Callable[[], str]  # the card in full
    words: str = ""  # more text to find it by

    @property
    def kind(self) -> str:
        return self.id.partition(":")[0]

    @property
    def line(self) -> str:
        return f"{self.id} — {self.summary}"


def _one_line(text: str | None, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tokens(text: str) -> list[str]:
    """Lowercase words without accents; a final `s` dropped, so a plural finds its singular."""
    text = unicodedata.normalize("NFKD", text.lower())
    words = re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


class Knowledge:
    def __init__(self, cards: list[Card]):
        self.cards = cards
        self._by_id = {c.id: c for c in cards}
        self._words = [Counter(_tokens(f"{c.id} {c.summary} {c.words}")) for c in cards]
        self._frequency = Counter(w for words in self._words for w in words)

    @property
    def complete(self) -> bool:
        """The repository is small enough to list whole: there is nothing left to search for."""
        return sum(c.kind in DATA for c in self.cards) <= LISTED

    def get(self, identifier: str) -> Card | None:
        """The card by its id; the kind may be left out when the name belongs to one card only."""
        identifier = identifier.strip().strip("`'\"")
        if identifier in self._by_id:
            return self._by_id[identifier]
        name = identifier.partition(":")[2] or identifier
        matches = [c for c in self.cards if (own := c.id.partition(":")[2]) == name or own.endswith(f".{name}")]
        return matches[0] if len(matches) == 1 else None

    def search(self, text: str, limit: int = 6, kinds: tuple[str, ...] = ()) -> list[Card]:
        """The cards (of the kinds given, or of any) that best match the words (BM25), best first."""
        wanted = set(_tokens(text))
        average = sum(sum(words.values()) for words in self._words) / max(len(self.cards), 1)
        scored = []
        for card, words in zip(self.cards, self._words, strict=True):
            if kinds and card.kind not in kinds:
                continue
            length = sum(words.values())
            score = 0.0
            for word in wanted & set(words):
                rarity = math.log(1 + (len(self.cards) - self._frequency[word] + 0.5) / (self._frequency[word] + 0.5))
                score += rarity * words[word] * 2.5 / (words[word] + 1.5 * (0.25 + 0.75 * length / average))
            if score:
                scored.append((score, card))
        return [card for _, card in sorted(scored, key=lambda pair: -pair[0])[:limit]]


# --- the cards -----------------------------------------------------------------------------------------


def build(parser: argparse.ArgumentParser, project=None, catalog=None) -> Knowledge:
    """The cards of the command line and, inside a data repository, of what it contains."""
    cards = _commands(parser)
    if project is not None:
        cards += _queries(project) + _metrics(project) + _tables(project, catalog) + _products(project)
    return Knowledge(cards)


def _commands(parser: argparse.ArgumentParser) -> list[Card]:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help or "" for a in sub._choices_actions}
    return [
        Card(f"command:{name}", _one_line(helps.get(name)), command.format_help)
        for name, command in sub.choices.items()
        if name != "copilot"
    ]


def _queries(project) -> list[Card]:
    from statera.consume import queries

    cards = []
    for q in queries.load(project):
        name = q.id.rsplit(".", 1)[-1]
        parameters = ", ".join(
            p["name"] + ("" if p.get("required") else "?") for p in q.meta.get("parameters", [])
        )  # fmt: skip
        summary = f"{q.title}. {_one_line(q.meta.get('description'))} Parameters: {parameters or 'none'}"
        words = f"{q.meta.get('theme', '')} {q.meta.get('product', '')} {' '.join(q.meta.get('bases', []))}"
        cards.append(Card(f"query:{name}", summary, lambda q=q: queries.describe(q.id, project), words))
    return cards


def _metrics(project) -> list[Card]:
    from statera.consume import metrics

    cards = []
    for name, m in metrics.definitions(project).items():
        dimensions = sorted({d for s in m.get("sources", []) for d in s["dimensions"]})
        by = f" By: {', '.join(dimensions)}." if dimensions else ""
        summary = f"{m.get('label', name)}. {_one_line(m.get('description'))}{by}"
        cards.append(Card(f"metric:{name}", summary, lambda name=name, m=m: _metric_detail(name, m)))
    return cards


def _metric_detail(name: str, m: dict) -> str:
    lines = [f"metric {name} — {m.get('label', name)} ({m['type']})", _one_line(m.get("description"), 400)]
    if m["type"] == "ratio":
        lines.append(f"= {m['numerator']} / {m['denominator']} × {m.get('factor', 1)} (same options as them)")
    for source in m.get("sources", []):
        lines.append(f"from {source['table']}; --by and --filter accept: {', '.join(source['dimensions'])}")
    lines += [
        "Examples:",
        f"  statera metric {name} --period 2026-06",
        f"  statera metric {name} --by <dimension> --from 2026-01 --to 2026-12 --filter <dimension>=<value>",
        "A whole year is --from YYYY-01 --to YYYY-12; the result has one row per period.",
    ]
    return "\n".join(lines)


def _tables(project, catalog) -> list[Card]:
    from statera.consume import metrics, queries

    used_by: dict[str, list[str]] = {}
    for q in queries.load(project):
        for base in q.meta.get("bases", []):
            used_by.setdefault(base, []).append(f"query:{q.id.rsplit('.', 1)[-1]}")
    for name, m in metrics.definitions(project).items():
        for source in m.get("sources", []):
            used_by.setdefault(source["table"], []).append(f"metric:{name}")

    cards = []
    for table in sorted(catalog._contracts, key=lambda t: (LAYER_ORDER.get(t.split(".")[0], 9), t)):
        try:
            d = catalog.describe(table)
        except KeyError:  # a contract without a description: `statera validate` says so; the copilot goes on
            continue
        columns = [c["name"] for c in d.columns]
        notes = catalog.notes(table)
        if table in used_by:
            notes.append(f"Already answered by: {', '.join(used_by[table])} (prefer them when they fit).")
        detail = "\n".join([str(d), *(f"  note: {n}" for n in notes)])
        summary = f"[{d.layer}, product {d.unit}] {_one_line(d.purpose)} Columns: {', '.join(columns)}"
        words = " ".join(c["description"] for c in d.columns)
        cards.append(Card(f"table:{table}", summary, lambda detail=detail: detail, words))
    return cards


def _products(project) -> list[Card]:
    cards = []
    for unit in project.products():
        odps = unit.odps
        description = odps.get("description") or {}
        kind = f"internal, {unit.alignment}" if unit.internal else "dissemination"
        summary = f"[{kind}] {odps.get('name', unit.name)}. {_one_line(description.get('purpose'))}"
        key = "--cut-date YYYY-MM-DD" if unit.internal else "--period YYYY-MM"
        lines = [
            f"product {unit.name} — {odps.get('name', unit.name)} ({kind})",
            *(f"  {k}: {_one_line(v, 400)}" for k, v in description.items() if v),
            *(f"  output {p['name']}: {_one_line(p.get('description'))}" for p in odps.get("outputPorts", [])),
            "Commands:",
            f"  statera status --product {unit.name}            # every edition; with {key}: one in detail",
            f"  statera run --product {unit.name} {key}",
        ]
        cards.append(Card(f"product:{unit.name}", summary, lambda lines=lines: "\n".join(lines)))
    return cards
