"""Finding the classes a process names: in the product first, then in the framework (specification 05).

A product's classes live in fixed files only: `<phase>/tasks.py` (Task, Await), `6-analyse/checks.py`
(Check) and `3-build/synthetic.py` (SyntheticSource). A class name is unique in its product; a product class
with the name of a framework class must subclass it.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from statera.extend import framework
from statera.extend.base import ABSTRACTIONS, Await, Check, SyntheticSource, Task


class ExtensionError(LookupError):
    """A process or a control names a class that does not exist, or of the wrong kind."""


@dataclass
class ProductClasses:
    classes: dict[str, type] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)  # class name -> file that defines it
    problems: list[str] = field(default_factory=list)  # duplicates, shadowing, import errors


def product_files(unit) -> list[Path]:
    files = sorted(unit.folder.glob("[1-8]-*/tasks.py"))
    files += [f for f in (unit.phase(6) / "checks.py", unit.phase(3) / "synthetic.py") if f.exists()]
    return files


_MODULES: dict[tuple[str, float], object] = {}


def _load(path: Path):
    key = (str(path.resolve()), path.stat().st_mtime)
    if key not in _MODULES:
        name = "statera_extension_" + hashlib.sha1(key[0].encode()).hexdigest()[:12]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)  # defines the classes; runs no step
        _MODULES[key] = module
    return _MODULES[key]


def product_classes(unit) -> ProductClasses:
    found = ProductClasses()
    for path in product_files(unit):
        try:
            module = _load(path)
        except Exception as error:  # noqa: BLE001 - reported by validation
            found.problems.append(f"{unit.project.rel(path)} does not import: {error}")
            continue
        for obj in vars(module).values():
            if not (isinstance(obj, type) and issubclass(obj, ABSTRACTIONS) and obj.__module__ == module.__name__):
                continue
            if obj.__name__ in found.classes:
                found.problems.append(
                    f"{unit.name}: class {obj.__name__} defined twice ({unit.project.rel(found.files[obj.__name__])}, "
                    f"{unit.project.rel(path)}); a class name is unique in its product"
                )
                continue
            base = framework.FRAMEWORK.get(obj.__name__)
            if base is not None and not issubclass(obj, base):
                found.problems.append(
                    f"{unit.name}: class {obj.__name__} has the name of a framework class but does not subclass it "
                    f"(class {obj.__name__}(statera.extend.{base.__name__}))"
                )
            found.classes[obj.__name__] = obj
            found.files[obj.__name__] = path
    return found


KIND_NAME = {Task: "Task", Await: "Await", Check: "Check", SyntheticSource: "SyntheticSource"}


def resolve(unit, name: str, kind: type) -> type:
    """The class `name` of the given abstraction: the product's, else the framework's."""
    cls = product_classes(unit).classes.get(name) or framework.FRAMEWORK.get(name)
    if cls is None:
        raise ExtensionError(f"{unit.name}: no class {name} (in the product's tasks/checks files or the framework)")
    if not issubclass(cls, kind):
        raise ExtensionError(f"{unit.name}: {name} is not a {KIND_NAME[kind]}")
    return cls


def check_class(unit, control: dict) -> type:
    """The Check of a control in controls.yaml: `check: <Class>`, or the legacy `type:` alias."""
    name = control.get("check") or framework.CONTROL_TYPES.get(control.get("type", ""))
    if not name:
        raise ExtensionError(f"{unit.name}: control {control.get('id')} declares neither `check` nor a known `type`")
    return resolve(unit, name, Check)


@dataclass
class Binding:
    """What runs for an `implementation` or `awaits` of the process."""

    cls: type | None = None
    function: Callable | None = None
    deprecated: str | None = None  # the replacement, when the process uses a `py:` form


def _function(reference: str) -> Callable:
    module, _, name = reference.removeprefix("py:").rpartition(".")
    return getattr(importlib.import_module(module), name)


def bind(unit, reference: str, kind: type) -> Binding:
    """Resolves `implementation` (Task) or `awaits` (Await): a class name, a legacy framework function
    (run as its class), `dbt:build`, or a product's own `py:module.function` (deprecated)."""
    if reference in framework.LEGACY:
        name = framework.LEGACY[reference]
        return Binding(cls=resolve(unit, name, kind), deprecated=name if reference.startswith("py:") else None)
    if reference.startswith("py:"):
        return Binding(function=_function(reference), deprecated="a Task or Await class")
    return Binding(cls=resolve(unit, reference, kind))


def warn_deprecated(reference: str, replacement: str) -> None:
    warnings.warn(
        f"`{reference}` in a process is deprecated since statera 0.6: use {replacement}",
        DeprecationWarning,
        stacklevel=3,
    )
