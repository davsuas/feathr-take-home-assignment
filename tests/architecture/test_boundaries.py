"""map #20 - the hexagonal boundary, as a test rather than a promise.

ARCHITECTURE.md claims the domain knows nothing of FastAPI, MongoDB, Redis,
Elasticsearch, or the queue. This is what makes that claim checkable in CI instead of
something a reviewer has to take on faith.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "eventplatform"
FORBIDDEN = {
    "fastapi",
    "starlette",
    "pymongo",
    "motor",
    "redis",
    "elasticsearch",
    "aio_pika",
    "boto3",
    "prometheus_client",
}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("layer", ["domain", "application", "ports"])
def test_domain_imports_no_infrastructure(layer: str) -> None:
    offenders: list[str] = []
    for path in (SRC / layer).rglob("*.py"):
        leaked = _imported_roots(path) & FORBIDDEN
        if leaked:
            offenders.append(f"{path.relative_to(SRC)} imports {sorted(leaked)}")
    assert not offenders, "inward layers must not import infrastructure:\n" + "\n".join(offenders)


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inward_layers_do_not_import_our_own_adapters(layer: str) -> None:
    offenders: list[str] = []
    for path in (SRC / layer).rglob("*.py"):
        text = path.read_text()
        for banned in ("eventplatform.infrastructure", "eventplatform.api", "eventplatform.worker"):
            if banned in text:
                offenders.append(f"{path.relative_to(SRC)} references {banned}")
    assert not offenders, "\n".join(offenders)


def test_import_linter_contracts_hold() -> None:
    """Runs the real contract file, so the two never drift apart."""
    result = subprocess.run(
        [str(Path(sys.executable).with_name("lint-imports"))],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_event_repository_exposes_no_mutation() -> None:
    """FR-010: immutability is enforced by the absence of the method.

    If someone adds ``update`` or ``delete`` to the repository port, immutability stops
    being structural and becomes a convention. This fails when that happens.
    """
    from eventplatform.ports.event_repository import EventRepository

    methods = {m for m in dir(EventRepository) if not m.startswith("_")}
    assert "update" not in methods
    assert "delete" not in methods
    assert "update_projection" in methods  # the single, narrow exception
