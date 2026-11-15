"""statera — framework for the statistical product repository standard (working name).

The framework is generic: it knows no product. Everything specific to an organization (dissemination and
internal products, reference tables, queries, recipes) lives in a CONTENT repository, described by a
`statera.toml` file at its root. See `specification/` in the framework repository.

Package layout by GSBPM 5.1 phase:

    design/       processes in YAML <-> BPMN 2.0 (phase 2, Design)
    collect/      dated extraction into bronze, driven by the contracts (phase 4, Collect)
    process/      generated dbt project, business rules -> unit tests (phase 5, Process)
    analyse/      ODCS contracts, declarative controls, statistical disclosure control (phase 6, Analyse)
    disseminate/  edition package, Data Package, editions register (phase 7, Disseminate)
    evaluate/     declarative quality indicators (phase 8, Evaluate)
    consume/      query library, metrics, recurring queries, recipes
    execution.py  runs the product's process, task by task
"""

__version__ = "0.9.3"
SPECIFICATION = "0.1"
