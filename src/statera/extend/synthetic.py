"""Running the synthetic sources of the products (`statera synthetic`, `statera demo`; specification 05).

Each product may stand in for its source systems with `SyntheticSource` classes in `3-build/synthetic.py`.
They run in dependency order (the internal products a product reads come first), and a `fault` is given only
to the sources that declare it. A repository with no such class may still declare the statera 0.5 hook
`[demo] synthetic = "file.py:function"` in statera.toml (deprecated; accepted in 0.6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from statera.extend.base import SyntheticContext, SyntheticSource
from statera.extend.registry import product_classes


class SyntheticError(ValueError):
    """No synthetic source to run, a fault no source declares, or a synthetic.py that does not import."""


@dataclass
class Generated:
    """What one synthetic source (or the legacy hook) generated: rows written per table."""

    product: str | None
    source: str
    counts: dict[str, int] = field(default_factory=dict)
    fault: str | None = None


def ordered_products(project) -> list:
    """The project's products, each after the internal products it reads (ODPS input ports)."""
    from statera.ports import input_products

    ordered, visiting, done = [], set(), set()

    def visit(unit) -> None:
        if unit.name in done or unit.name in visiting:  # a cycle is validation's to report
            return
        visiting.add(unit.name)
        for dependency in input_products(unit):
            visit(dependency)
        visiting.discard(unit.name)
        done.add(unit.name)
        ordered.append(unit)

    for unit in project.products():
        visit(unit)
    return ordered


def sources(project, product: str | None = None) -> list[tuple[object, type[SyntheticSource]]]:
    """(unit, class) of every synthetic source, in dependency order, of the project or of one product."""
    from statera.project import ProjectError

    try:
        units = [project.product(product)] if product else ordered_products(project)
    except ProjectError as error:
        raise SyntheticError(str(error)) from error
    found = []
    for unit in units:
        file = unit.phase(3) / "synthetic.py"
        if not file.exists():
            continue
        classes = product_classes(unit)
        broken = [p for p in classes.problems if project.rel(file) in p]
        if broken:
            raise SyntheticError("; ".join(broken))
        found += [(unit, c) for c in classes.classes.values() if issubclass(c, SyntheticSource)]
    return found


def declared_faults(project, product: str | None = None) -> set[str]:
    return {f for _, cls in sources(project, product) for f in cls.faults}


def run(
    project,
    product: str | None = None,
    fault: str | None = None,
    references: bool = False,
    log: Callable[[str], None] = print,
) -> list[Generated]:
    """Runs the synthetic sources (of every product, or of one) and returns what each generated."""
    found = sources(project, product)
    if not found:
        return _legacy(project, product, fault, references)
    declared = {f for _, cls in found for f in cls.faults}
    if fault and fault not in declared:
        where = f"product {product}" if product else "any product"
        raise SyntheticError(
            f"fault {fault!r} is not declared by any synthetic source of {where}"
            + (f" (declared: {', '.join(sorted(declared))})" if declared else " (none declares faults)")
        )
    generated = []
    for unit, cls in found:
        injected = fault if fault in cls.faults else None
        ctx = SyntheticContext(project, unit, references=references, log=log)
        counts = cls().generate(ctx, injected) or {}
        generated.append(Generated(unit.name, cls.__name__, dict(counts), injected))
    return generated


def _legacy(project, product: str | None, fault: str | None, references: bool) -> list[Generated]:
    hook = project.demo.get("synthetic")
    if not hook:
        where = f"product {product} has no" if product else "no product has"
        raise SyntheticError(
            f"{where} synthetic sources: write SyntheticSource classes in <product>/3-build/synthetic.py "
            "(statera.extend)"
        )
    import warnings

    warnings.warn(
        "[demo] synthetic in statera.toml is deprecated since statera 0.6: write SyntheticSource classes in "
        "<product>/3-build/synthetic.py",
        DeprecationWarning,
        stacklevel=3,
    )
    counts = project.hook(hook)(project, failure=fault, references=references)
    return [Generated(None, hook, dict(counts or {}), fault)]
