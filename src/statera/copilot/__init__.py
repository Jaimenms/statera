"""The copilot: ask in plain language, get the `statera` command to run (optional, extra `copilot`).

    statera copilot "how many records did SP have in 2026?"
    statera copilot                       # a conversation: each answer can be refined

The command line grew with the standard; the copilot is a way in for someone who knows the question and
not the command. It answers with ONE of: a `statera` command (a cataloged query, an official metric, the
status of an edition…), a SQL query over the cataloged tables when nothing cataloged answers, a question
back when the request is ambiguous, or a plain explanation. It runs what it proposes only when the person
agrees.

How it works (no agent framework: the agent is `agent.py`, a few short calls to the model per question):

    knowledge.py   what the model is shown: one CARD per command, table (contract), cataloged query,
                   metric and product, built from the repository at each start. Nothing to index or keep
                   up to date. A repository too big to list is searched by the words of the question, and
                   the model may ask for another search.
    agent.py       choose, fill, check. The model first CHOOSES what answers (the ids of the cards are an
                   enum: it cannot name what does not exist), then FILLS only that in, seeing only its
                   card (the schema of the reply is built from what the query, the metric or the command
                   declares: an undeclared parameter or option cannot be written). Small decisions, each
                   constrained by a JSON schema the server holds the reply to, which is what lets a small
                   local model do this.
    sql.py         an ad hoc query is checked before anyone sees it: one read-only SELECT, only cataloged
                   tables the role may read, the engine's own EXPLAIN (a column that does not exist goes
                   back to the model as an error, to fix) and the values it filters by (which must exist).
    llm.py         the model: an Ollama server ON THIS COMPUTER. The address must be a loopback one; there
                   is no client for a hosted model, by design: the catalog and the data never leave.
    console.py     the terminal: shows what was chosen and why, asks before running, runs.

What it proposes is checked, not trusted: a command must be accepted by the real command line (and name a
query, a metric and a product that exist, with valid parameters) and a SQL query must pass `sql.py`;
otherwise the error goes back to the model, never to the person. What it may run: see `console.policy`
(what only reads, after a yes; what produces, after a yes every time; what records a human decision or
deletes, never).

Configuration, all optional, in statera.toml:

    [copilot]
    model = "qwen2.5-coder:7b"         # any model the local Ollama has (also STATERA_COPILOT_MODEL, --model)
    url = "http://localhost:11434"     # the local Ollama (also STATERA_COPILOT_URL); loopback only
    context = 8192                     # context window, in tokens
"""

from statera.copilot.agent import Agent, Outcome
from statera.copilot.llm import CopilotError, Ollama

__all__ = ["Agent", "CopilotError", "Ollama", "Outcome"]
