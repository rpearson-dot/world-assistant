"""The module boundaries (app/modules/sdk.py, docs/module-sdk.md): the core never imports a module; a module imports
only the core's published interface, its own folder, and the modules it declares in `needs` or `uses`; and what a
module provides is at a known extension point and can be loaded."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.modules import registry
from app.modules.sdk import CORE, POINTS

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
MODULES = APP / "modules"


def _name(p: Path) -> str:
    parts = list(p.relative_to(ROOT).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _owner(name: str) -> str | None:
    """The module a dotted name belongs to, or None for the core (the registry's own files count as core)."""
    parts = name.split(".")
    if len(parts) >= 3 and parts[1] == "modules" and (MODULES / parts[2]).is_dir():
        return parts[2]
    return None


def _imports(p: Path) -> list[tuple[int, str]]:
    """Every app.* name a file imports, absolute, with its line."""
    name = _name(p)
    pkg = name if p.name == "__init__.py" else name.rsplit(".", 1)[0]
    out = []
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                b = pkg.split(".")
                b = b[: len(b) - (n.level - 1)] if n.level > 1 else b
                base = ".".join(b + ([base] if base else []))
            out += [(n.lineno, base)] + [(n.lineno, f"{base}.{a.name}") for a in n.names]
    return [(ln, m) for ln, m in out if m == "app" or m.startswith("app.")]


def _real(name: str) -> str:
    """The longest prefix of a dotted import that is a file or package (drops imported attributes)."""
    while name:
        path = ROOT / Path(*name.split("."))
        if path.with_suffix(".py").exists() or path.is_dir():
            return name
        name = name.rsplit(".", 1)[0] if "." in name else ""
    return name


FILES = sorted(APP.rglob("*.py"))


def test_the_core_never_imports_a_module() -> None:
    bad = []
    for p in FILES:
        if _owner(_name(p)) is not None:
            continue
        for ln, m in _imports(p):
            if _owner(_real(m)) is not None:
                bad.append(f"{p.relative_to(ROOT)}:{ln} imports {_real(m)}: ask the registry (provides) instead")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("module", sorted(m.id for m in registry.modules() if (MODULES / m.id).is_dir()))
def test_a_module_imports_only_the_sdk_itself_and_what_it_declares(module: str) -> None:
    m = registry.get(module)
    assert m is not None
    allowed = {module, *m.needs, *m.uses}
    bad = []
    for p in sorted((MODULES / module).rglob("*.py")):
        for ln, name in _imports(p):
            real = _real(name)
            other = _owner(real)
            if other is not None:
                if other not in allowed:
                    bad.append(f"{p.relative_to(ROOT)}:{ln} imports {real}: declare uses=({other!r},) or needs")
            elif real not in ("app", "app.modules") and not any(real == c or real.startswith(c + ".") for c in CORE):
                bad.append(f"{p.relative_to(ROOT)}:{ln} imports {real}: not part of the module SDK (app/modules/sdk.py)")
    assert not bad, "\n".join(bad)


def test_declared_uses_and_needs_are_real_modules_and_used() -> None:
    ids = {m.id for m in registry.modules()}
    for m in registry.modules():
        for other in (*m.needs, *m.uses):
            assert other in ids and other != m.id, (m.id, other)


def test_what_a_module_provides_is_at_a_known_point_and_loads() -> None:
    for m in registry.modules():
        for point, ref in m.provides.items():
            assert point in POINTS, f"{m.id} provides {point!r}, which is not an extension point (app/modules/sdk.py)"
            assert registry.import_string(ref) is not None, (m.id, ref)


def test_every_module_has_its_own_folder_with_a_description() -> None:
    for m in registry.modules():
        if m.core or m.source != "builtin":
            continue
        folder = MODULES / m.id
        assert folder.is_dir() or not (m.routers or m.worker), f"{m.id}: its code belongs in app/modules/{m.id}/"
        if folder.is_dir():
            assert (folder / "README.md").exists(), f"app/modules/{m.id}/README.md: what it does and where its parts are"


def test_the_module_readmes_are_current() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("module_readmes", ROOT / "scripts" / "module-readmes.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    stale = [mid for mid in mod.folders() if (MODULES / mid / "README.md").read_text() != mod.build(mid)]
    assert not stale, f"run: python scripts/module-readmes.py ({', '.join(stale)})"
