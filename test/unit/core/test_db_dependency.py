# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Guard the single definition of the request-scoped database dependency.

``scope="function"`` on :data:`src.core.db.DbSession` (``Depends(get_db, scope="function")``)
is what commits a request's transaction before its response is sent (commit 69878f2). Routes
depend on it as ``db: AsyncSession = DbSession``; the ``scope`` must not be respelled at a
call site, so ``get_db`` itself stays private to ``core/db.py``.
"""

from pathlib import Path

import pytest

import src.core.db

_DB_MODULE = Path(src.core.db.__file__).resolve()
_SRC_ROOT = _DB_MODULE.parents[1]
_PYTHON_FILES = sorted(_SRC_ROOT.rglob("*.py"))


def _relative(path: Path) -> str:
    return str(path.relative_to(_SRC_ROOT.parent))


@pytest.mark.parametrize("path", _PYTHON_FILES, ids=_relative)
def test_no_bare_get_db_dependency(path: Path) -> None:
    """Only ``core/db.py`` may wire ``get_db`` into ``Depends`` (and fix its scope)."""
    if path == _DB_MODULE:
        return
    assert "Depends(get_db" not in path.read_text(), (
        f"{_relative(path)} wires get_db into Depends directly; use src.core.db.DbSession instead"
    )


@pytest.mark.parametrize("path", _PYTHON_FILES, ids=_relative)
def test_get_db_is_referenced_only_in_core_db(path: Path) -> None:
    """API modules import ``DbSession``; ``get_db`` itself is private to ``core/db.py``."""
    if path == _DB_MODULE:
        return
    assert "get_db" not in path.read_text(), (
        f"{_relative(path)} references get_db; import DbSession from src.core.db instead"
    )
